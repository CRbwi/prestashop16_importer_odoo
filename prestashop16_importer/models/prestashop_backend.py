# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
import requests
import xml.etree.ElementTree as ET
import time
import base64

_logger = logging.getLogger(__name__)

class PrestashopBackend(models.Model):
    _name = 'prestashop16.backend'
    _description = 'Prestashop 1.6 Backend Configuration'
    _order = 'sequence,name'

    name = fields.Char(string='Name', required=True, help="A user-friendly name for this Prestashop connection.")
    sequence = fields.Integer(default=10)
    prestashop_url = fields.Char(string='Prestashop URL', required=True, help="The base URL of your Prestashop 1.6 store (e.g., http://yourstore.com).")
    api_key = fields.Char(string='API Key', required=True, help="The Prestashop Webservice Key.")
    active = fields.Boolean(default=True)

    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
        index=True,
        help="Company related to this Prestashop instance."
    )

    def _safe_database_operation(self, operation_func, operation_name="database operation"):
        """
        Safely execute database operations with transaction error handling
        This prevents psycopg2.errors.InFailedSqlTransaction errors
        """
        try:
            return operation_func()
        except Exception as e:
            _logger.error("❌ Failed %s: %s", operation_name, str(e))
            # Check if it's a transaction error
            if 'InFailedSqlTransaction' in str(e) or 'transaction is aborted' in str(e):
                _logger.warning("🔄 Transaction error detected, attempting recovery...")
                try:
                    # Force a rollback to clean state
                    self.env.cr.rollback()
                    # Try the operation again
                    return operation_func()
                except Exception as retry_error:
                    _logger.error("❌ Retry also failed for %s: %s", operation_name, str(retry_error))
                    raise retry_error
            else:
                raise e

    def _create_error_report(self, title, main_error, imported=0, skipped=0, errors=0, context=""):
        """Helper method to create detailed error notifications with enhanced debugging info"""
        if errors > 0 or imported == 0:
            # Create detailed error message with timestamp
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            error_details = f"""🕐 TIMESTAMP: {timestamp}

📊 IMPORT SUMMARY:
• ✅ Imported: {imported} new records
• ⚠️ Skipped: {skipped} (already exist or invalid)
• ❌ Errors: {errors} failed to process

🔍 ERROR DETAILS:
{main_error}

💡 CONTEXT & SOLUTIONS:
{context}

🛠️ NEXT TROUBLESHOOTING STEPS:
1. 🔗 Test connection first (use 'Test Connection' button)
2. 🌐 Check your internet connection stability
3. 🖥️ Verify Prestashop server is running and responding
4. 🔑 Check API key permissions in Prestashop admin
5. 📋 Look at Odoo server logs for technical details
6. ⏰ Try importing during off-peak hours if server is slow
7. 📞 Contact system administrator if problem persists

📍 CURRENT STATUS: Import was {('COMPLETED WITH ERRORS' if errors > 0 else 'STOPPED')}"""

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': title,
                    'message': error_details,
                    'type': 'danger',
                    'sticky': True,  # Keep error notifications visible until manually closed
                }
            }
        else:
            # Success message
            return {
                'type': 'ir.actions.client', 
                'tag': 'display_notification',
                'params': {
                    'title': title,
                    'message': f'✅ Import successful!\n\n• Imported: {imported} items\n• Skipped: {skipped} (already exist)',
                    'type': 'success',
                    'sticky': False,
                }
            }

    def _log_import_progress(self, current, total, imported, skipped, errors, item_type):
        """Helper method to log detailed import progress"""
        percentage = (current / total * 100) if total > 0 else 0
        _logger.info(
            "🔄 %s IMPORT PROGRESS: %.1f%% (%d/%d) | ✅ Imported: %d | ⚠️ Skipped: %d | ❌ Errors: %d",
            item_type.upper(), percentage, current, total, imported, skipped, errors
        )
        
        # Log warning if too many errors
        if errors > 0 and (errors / max(current, 1)) > 0.2:  # More than 20% error rate
            _logger.warning(
                "⚠️ HIGH ERROR RATE detected in %s import: %d errors out of %d processed (%.1f%%)",
                item_type, errors, current, (errors / current * 100)
            )

    def action_test_connection(self):
        """Test connection to Prestashop webservice with detailed diagnostics"""
        self.ensure_one()
        
        # Validate URL format
        if not self.prestashop_url or not self.api_key:
            raise UserError("Please fill in both Prestashop URL and API Key before testing the connection.")
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        try:
            # Step 1: Test basic URL accessibility
            _logger.info("Step 1: Testing basic URL accessibility...")
            try:
                response = requests.get(test_url, timeout=10)
                url_status = f"URL responds with status: {response.status_code}"
                if response.status_code == 200:
                    content_preview = response.text[:500]
                    if '<?xml' in content_preview:
                        url_result = "✓ URL accessible and returns XML"
                    else:
                        url_result = f"⚠ URL accessible but returns HTML (not XML): {content_preview[:100]}..."
                elif response.status_code == 401:
                    url_result = "⚠ URL accessible but returns 401 Unauthorized"
                elif response.status_code == 404:
                    url_result = "✗ URL returns 404 Not Found - Check if /api path is correct"
                else:
                    url_result = f"⚠ URL returns status {response.status_code}"
            except requests.exceptions.ConnectionError:
                url_result = "✗ Cannot connect to URL - Check if server is running"
            except requests.exceptions.Timeout:
                url_result = "✗ Connection timeout - Server may be slow"
            except Exception as e:
                url_result = f"✗ URL test failed: {str(e)}"
            
            # Step 2: Test with authentication
            _logger.info("Step 2: Testing with API authentication...")
            try:
                auth_url = f"{test_url}?ws_key={self.api_key}"
                auth_response = requests.get(auth_url, timeout=10)
                if auth_response.status_code == 200:
                    auth_result = "✓ Authentication successful"
                elif auth_response.status_code == 401:
                    auth_result = "✗ Authentication failed - Check API key"
                elif auth_response.status_code == 403:
                    auth_result = "✗ Access forbidden - Check API key permissions"
                else:
                    auth_result = f"⚠ Auth test returns status {auth_response.status_code}"
            except Exception as e:
                auth_result = f"✗ Auth test failed: {str(e)}"
            
            # Step 3: Test with lightweight endpoint
            _logger.info("Step 3: Testing with languages endpoint (lightweight test)...")
            try:
                languages_url = f"{test_url}/languages?ws_key={self.api_key}&limit=1"
                lang_response = requests.get(languages_url, timeout=15)
                if lang_response.status_code == 200:
                    prestapyt_result = "✓ Languages endpoint accessible - Connection working"
                    # If we get here, show success
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': '✅ Connection Success!',
                            'message': f'Successfully connected to Prestashop webservice at: {test_url}',
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                else:
                    prestapyt_result = f"⚠ Languages endpoint failed with status {lang_response.status_code}"
            except Exception as e:
                prestapyt_result = f"✗ API test failed: {str(e)}"
            
            # Generate diagnostic report
            diagnostic_report = f"""❌ Connection Failed - Diagnostic Report:

URL tested: {test_url}
API Key: {self.api_key[:8]}...{self.api_key[-4:] if len(self.api_key) > 12 else '(too short)'}

Results:
1. Basic URL Test: {url_result}
2. Authentication Test: {auth_result}  
3. API Endpoint Test: {prestapyt_result}

TROUBLESHOOTING GUIDE:

If URL is not accessible:
- Check if Prestashop is running
- Verify the base URL is correct
- Test manually: {test_url.replace('/api', '')}

If URL is accessible but returns HTML:
- Check .htaccess file in your Prestashop root
- Ensure mod_rewrite is enabled
- Add these lines to .htaccess if missing:
  RewriteRule ^api/?(.*)$ webservice/dispatcher.php?url=$1 [QSA,L]

If Authentication fails:
- Go to Prestashop Admin > Advanced Parameters > Webservice
- Enable webservice: YES
- Check your API key permissions
- Verify API key has access to: shops, customers, products, categories

If still failing, check server logs for more details."""

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '❌ Connection Test Failed!',
                    'message': diagnostic_report,
                    'type': 'danger',
                    'sticky': True,  # Keep error visible
                }
            }
            
        except ImportError:
            raise UserError("The 'requests' Python library is not available. Please contact system administrator.")
        except Exception as general_error:
            raise UserError(f"Connection test failed: {str(general_error)}")

    def action_test_url_manually(self):
        """Test only URL accessibility without API authentication"""
        self.ensure_one()
        
        if not self.prestashop_url:
            raise UserError("Please fill in the Prestashop URL before testing.")
        
        # Test different URL combinations
        base_url = self.prestashop_url.rstrip('/')
        test_urls = [
            base_url,
            f"{base_url}/api",
            f"{base_url}/webservice",
            f"{base_url}/api/",
        ]
        
        try:
            results = []
            
            for url in test_urls:
                try:
                    response = requests.get(url, timeout=5)
                    if response.status_code == 200:
                        if '<?xml' in response.text:
                            results.append(f"✓ {url} - Returns XML (Good!)")
                        else:
                            preview = response.text[:200].replace('\n', ' ')
                            results.append(f"⚠ {url} - Returns HTML: {preview}...")
                    elif response.status_code == 401:
                        results.append(f"✓ {url} - Returns 401 (API endpoint found, needs auth)")
                    elif response.status_code == 404:
                        results.append(f"✗ {url} - Returns 404 (Not found)")
                    else:
                        results.append(f"⚠ {url} - Returns {response.status_code}")
                except requests.exceptions.ConnectionError:
                    results.append(f"✗ {url} - Connection failed")
                except requests.exceptions.Timeout:
                    results.append(f"✗ {url} - Timeout")
                except Exception as e:
                    results.append(f"✗ {url} - Error: {str(e)}")
            
            report = "URL Test Results:\n\n" + "\n".join(results)
            report += f"""

RECOMMENDATIONS:
- If any URL returns XML or 401, that's your correct API endpoint
- If all return HTML, check your .htaccess configuration
- If connection fails, verify your Prestashop is running

.htaccess should contain:
RewriteRule ^api/?(.*)$ webservice/dispatcher.php?url=$1 [QSA,L]"""
            
            raise UserError(report)
            
        except ImportError:
            # Fallback if requests is not available
            raise UserError(f"Testing URL: {base_url}/api\n\nPlease manually check if this URL is accessible in your browser.\nIt should return XML or ask for authentication.")

    def action_import_customers(self):
        """Import customers from Prestashop with detailed error handling and connection management"""
        self.ensure_one()
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        # Create a session for connection reuse
        session = requests.Session()
        session.headers.update({'User-Agent': 'Odoo-Prestashop-Importer/1.0'})
        
        # Disable mail tracking and computed fields during import to prevent transaction errors
        context = dict(self.env.context)
        context.update({
            'tracking_disable': True,
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'mail_auto_subscribe_no_notify': True,
            'no_reset_password': True,
        })
        
        try:
            # Get customers list with shorter, more realistic timeout
            customers_url = f"{test_url}/customers?ws_key={self.api_key}"
            _logger.info("🚀 Starting customer import from: %s", customers_url)
            
            try:
                response = session.get(customers_url, timeout=30)  # Reduced to 30 seconds
                _logger.info("✅ Successfully got customer list in %.2f seconds", response.elapsed.total_seconds())
            except requests.exceptions.Timeout:
                return self._create_error_report(
                    "❌ TIMEOUT ERROR - Customer Import Failed",
                    "Connection timeout while getting customer list (>30 seconds)",
                    context="""TIMEOUT SOLUTIONS:
• Your Prestashop server is too slow (>30s for customer list)
• Try importing during off-peak hours (night/weekend)
• Contact your hosting provider about server performance
• Check if other plugins are slowing down your server
• Consider upgrading your hosting plan
• Check server logs for PHP memory/execution time limits"""
                )
            except requests.exceptions.ConnectionError:
                return self._create_error_report(
                    "❌ CONNECTION ERROR - Customer Import Failed", 
                    "Cannot connect to Prestashop server",
                    context="""CONNECTION SOLUTIONS:
• Check your internet connection
• Verify Prestashop URL is correct and accessible
• Check if Prestashop server is running
• Verify firewall/security settings
• Test the URL manually in a browser
• Check if server is behind a firewall or CDN"""
                )
            
            if response.status_code != 200:
                return self._create_error_report(
                    "❌ HTTP ERROR - Customer Import Failed",
                    f"Prestashop API returned HTTP {response.status_code}",
                    context=f"""HTTP ERROR DETAILS:
Status Code: {response.status_code}
Response: {response.text[:500]}...

COMMON HTTP ERRORS:
• 401 Unauthorized: Invalid API key
• 403 Forbidden: API key lacks permissions  
• 404 Not Found: Wrong URL or API endpoint
• 500 Server Error: Prestashop server problem
• 503 Service Unavailable: Server overloaded

SOLUTIONS:
• Check API key in Prestashop admin
• Verify webservice permissions for customers
• Test connection first"""
                )
            
            # Parse XML response
            try:
                root = ET.fromstring(response.content)
                customers = root.findall('.//customer')
            except ET.ParseError as e:
                return self._create_error_report(
                    "❌ XML PARSE ERROR - Customer Import Failed",
                    f"Invalid XML response from Prestashop API: {str(e)}",
                    context=f"""XML ERROR DETAILS:
The server returned invalid XML data.

POSSIBLE CAUSES:
• Server returned HTML instead of XML (check .htaccess)
• Server error or crash during request
• API endpoint not properly configured
• Memory or timeout issues on Prestashop server

SOLUTIONS:
• Check Prestashop .htaccess file configuration
• Verify webservice is enabled in Prestashop admin
• Check server error logs
• Test API endpoint manually in browser"""
                )
            
            if not customers:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '⚠️ No Customers Found!',
                        'message': 'No customers were found in your Prestashop store.\n\nPossible reasons:\n- Store has no customers yet\n- API permissions are limited\n- Connection or server issues\n\nCheck your Prestashop admin panel to verify customers exist.',
                        'type': 'warning',
                        'sticky': True,
                    }
                }
            
            imported_count = 0
            skipped_count = 0
            error_count = 0
            partner_model = self.env['res.partner'].with_context(context)
            
            _logger.info("Found %d customers to process", len(customers))
            
            for i, customer in enumerate(customers):
                customer_id = customer.get('id')
                if not customer_id:
                    skipped_count += 1
                    continue
                
                # Early exit if too many consecutive errors
                if error_count > 10 and (error_count / max(i + 1, 1)) > 0.3:
                    _logger.error("🚨 Too many errors (%d/%d = %.1f%%), stopping import", error_count, i + 1, (error_count / (i + 1) * 100))
                    return self._create_error_report(
                        "❌ IMPORT STOPPED - Too Many Errors!",
                        f"Import stopped after {i + 1} customers due to high error rate ({error_count} errors)",
                        imported=imported_count,
                        skipped=skipped_count,
                        errors=error_count,
                        context="""HIGH ERROR RATE SOLUTIONS:
• Your Prestashop server may be overloaded or misconfigured
• Try importing during off-peak hours
• Check server resources (CPU, memory, database)
• Verify API permissions are correct
• Test connection stability first
• Contact hosting provider about server performance"""
                    )
                
                try:
                    # Get detailed customer data with improved retry logic and session reuse
                    customer_detail_url = f"{test_url}/customers/{customer_id}?ws_key={self.api_key}"
                    detail_response = None
                    
                    for attempt in range(3):  # Retry up to 3 times
                        try:
                            # Use session for connection reuse
                            detail_response = session.get(customer_detail_url, timeout=15)  # Reduced timeout
                            break
                        except requests.exceptions.Timeout:
                            _logger.warning("⏱️ Timeout getting customer %s (attempt %d/3)", customer_id, attempt + 1)
                            if attempt == 2:  # Last attempt
                                error_count += 1
                                break
                            time.sleep(2)  # Shorter wait between retries
                        except requests.exceptions.ConnectionError:
                            _logger.warning("🔌 Connection error getting customer %s (attempt %d/3)", customer_id, attempt + 1)
                            if attempt == 2:
                                error_count += 1
                                break
                            time.sleep(3)  # Wait before retry on connection error
                        except Exception as e:
                            _logger.warning("❌ Unexpected error getting customer %s: %s", customer_id, str(e))
                            if attempt == 2:
                                error_count += 1
                                break
                            time.sleep(2)
                    
                    if detail_response is None:
                        continue  # Skip this customer if all retries failed
                
                    if detail_response.status_code == 200:
                        try:
                            detail_root = ET.fromstring(detail_response.content)
                            customer_element = detail_root.find('.//customer')
                            
                            if customer_element is not None:
                                # Extract customer data
                                firstname = customer_element.find('firstname')
                                lastname = customer_element.find('lastname')
                                email = customer_element.find('email')
                                
                                firstname_text = firstname.text if firstname is not None else ''
                                lastname_text = lastname.text if lastname is not None else ''
                                email_text = email.text if email is not None else ''
                                
                                if email_text:
                                    # Check if customer already exists
                                    existing_partner = partner_model.search([
                                        ('email', '=', email_text),
                                        ('is_company', '=', False)
                                    ], limit=1)
                                    
                                    if not existing_partner:
                                        # Create customer with proper error handling
                                        def create_customer():
                                            partner_vals = {
                                                'name': f"{firstname_text} {lastname_text}".strip() or email_text,
                                                'email': email_text,
                                                'is_company': False,
                                                'customer_rank': 1,
                                                'comment': f"Imported from Prestashop (ID: {customer_id})",
                                            }
                                            return partner_model.create(partner_vals)
                                        
                                        try:
                                            partner = self._safe_database_operation(create_customer, f"customer creation {customer_id}")
                                            imported_count += 1
                                            _logger.info("Created customer: %s (Prestashop ID: %s)", partner.name, customer_id)
                                        except Exception as create_error:
                                            error_count += 1
                                            _logger.error("Failed to create customer %s: %s", customer_id, str(create_error))
                                            # Continue processing other customers instead of aborting
                                    else:
                                        skipped_count += 1
                                        _logger.debug("Customer already exists: %s", email_text)
                                else:
                                    skipped_count += 1
                                    _logger.warning("Customer %s has no email address", customer_id)
                            else:
                                error_count += 1
                                _logger.warning("No customer data found for ID %s", customer_id)
                        except ET.ParseError:
                            error_count += 1
                            _logger.warning("Invalid XML for customer %s", customer_id)
                    else:
                        error_count += 1
                        _logger.warning("Failed to get customer %s: HTTP %s", customer_id, detail_response.status_code)
                
                except Exception as e:
                    error_count += 1
                    _logger.error("💥 Error processing customer %s: %s", customer_id, str(e))
                
                # More frequent progress logging and connection monitoring
                if (i + 1) % 3 == 0:  # Every 3 customers instead of 5
                    self._log_import_progress(i + 1, len(customers), imported_count, skipped_count, error_count, "customer")
                    
                    # Check if we should pause due to errors
                    if error_count > 0 and (error_count / (i + 1)) > 0.2:  # >20% error rate
                        _logger.warning("⚠️ High error rate detected, adding longer pause...")
                        time.sleep(2)  # Longer pause when many errors
                
                # Shorter delay for normal operation, longer for errors
                if error_count > 0 and (error_count / max(i + 1, 1)) > 0.1:
                    time.sleep(1.0)  # 1 second delay when errors detected
                else:
                    time.sleep(0.3)  # Shorter delay for normal operation
                
                # Progress logging every 10 customers (with proper transaction handling)
                if (i + 1) % 10 == 0:
                    _logger.info("💾 Processed %d customers so far", i + 1)
                    # Force a transaction savepoint to prevent corruption
                    try:
                        self.env.cr.flush()
                    except Exception as flush_error:
                        _logger.warning("Transaction flush issue (non-critical): %s", str(flush_error))
            
            # Final report with detailed error information
            if error_count > 0:
                return self._create_error_report(
                    "⚠️ Customer Import Completed with ERRORS!",
                    f"Import process completed but encountered {error_count} errors",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Check server logs for detailed error information."
                )
            elif imported_count == 0:
                return self._create_error_report(
                    "⚠️ No Customers Imported!",
                    "No new customers were created during import",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Possible issues: All customers already exist, server problems, or API permissions."
                )
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '✅ Customer Import Successful!',
                        'message': f'Import completed successfully!\n\n• Imported: {imported_count} new customers\n• Skipped: {skipped_count} (already exist)',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            
        except Exception as e:
            _logger.error("Customer import failed: %s", str(e))
            return self._create_error_report(
                "💥 CRITICAL ERROR - Customer Import Failed!",
                f"Import process crashed with error: {str(e)}",
                context="""CRITICAL ERROR SOLUTIONS:
• Check internet connection
• Verify Prestashop URL and API key
• Test connection first
• Check server logs for technical details
• Contact system administrator if problem persists"""
            )

    def action_import_categories(self):
        """Import categories from Prestashop with enhanced error handling and connection management"""
        self.ensure_one()
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        # Create a session for connection reuse
        session = requests.Session()
        session.headers.update({'User-Agent': 'Odoo-Prestashop-Importer/1.0'})
        
        # Disable mail tracking and computed fields during import to prevent transaction errors
        context = dict(self.env.context)
        context.update({
            'tracking_disable': True,
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
        })
        
        imported_count = 0
        skipped_count = 0
        error_count = 0
        
        try:
            # Get categories from Prestashop with optimized timeout and limit
            categories_url = f"{test_url}/categories?ws_key={self.api_key}&limit=20"
            _logger.info("🚀 Starting category import from: %s", categories_url)
            
            try:
                response = session.get(categories_url, timeout=30)  # Using session for connection reuse
                _logger.info("✅ Successfully got category list in %.2f seconds", response.elapsed.total_seconds())
            except requests.exceptions.Timeout:
                return self._create_error_report(
                    "❌ TIMEOUT ERROR - Category Import Failed",
                    "Connection timeout while getting categories (30s)",
                    context="""TIMEOUT SOLUTIONS:
• Server is responding too slowly
• Try during off-peak hours
• Check server performance with hosting provider
• Consider reducing import batch size"""
                )
            except requests.exceptions.ConnectionError:
                return self._create_error_report(
                    "❌ CONNECTION ERROR - Category Import Failed",
                    "Cannot connect to Prestashop server",
                    context="""CONNECTION SOLUTIONS:
• Check internet connection
• Verify Prestashop URL and server status
• Test connection first
• Check firewall settings"""
                )
            
            if response.status_code != 200:
                return self._create_error_report(
                    "❌ HTTP ERROR - Category Import Failed",
                    f"Failed to get categories: HTTP {response.status_code}",
                    context=f"""HTTP ERROR DETAILS:
Status Code: {response.status_code}

SOLUTIONS:
• Check API key permissions for categories
• Verify webservice is enabled
• Test connection first"""
                )
            
            # Parse XML response
            try:
                root = ET.fromstring(response.content)
                categories = root.findall('.//category')
            except ET.ParseError as e:
                return self._create_error_report(
                    "❌ XML PARSE ERROR - Category Import Failed",
                    f"Invalid XML response for categories: {str(e)}",
                    context="Check server configuration and API endpoint"
                )
            
            _logger.info("Found %d categories to process", len(categories))
            
            # Create a session for reuse in category hierarchy processing
            category_model = self.env['product.category'].with_context(context)
            
            # First, collect all category data to establish proper import order
            categories_to_process = []
            categories_data = {}
            
            for category in categories:
                category_id = category.get('id')
                if not category_id or category_id in ['1', '2']:  # Skip root categories
                    skipped_count += 1
                    continue
                    
                try:
                    # Get detailed category data
                    category_detail_url = f"{test_url}/categories/{category_id}?ws_key={self.api_key}"
                    detail_response = session.get(category_detail_url, timeout=15)
                    
                    if detail_response.status_code == 200:
                        detail_root = ET.fromstring(detail_response.content)
                        category_element = detail_root.find('.//category')
                        
                        if category_element is not None:
                            name_elem = category_element.find('.//name/language')
                            if name_elem is None:
                                name_elem = category_element.find('name')
                            
                            parent_elem = category_element.find('id_parent')
                            
                            name_text = name_elem.text if name_elem is not None else f'Category {category_id}'
                            parent_id = parent_elem.text if parent_elem is not None else None
                            
                            if name_text and name_text.strip():
                                categories_data[category_id] = {
                                    'name': name_text.strip(),
                                    'parent_id': parent_id,
                                    'prestashop_id': category_id
                                }
                                categories_to_process.append(category_id)
                                _logger.debug("📁 Collected category: %s (Parent: %s)", name_text, parent_id)
                except Exception as e:
                    _logger.warning("❌ Error collecting data for category %s: %s", category_id, str(e))
                    error_count += 1
            
            _logger.info("🔄 Collected %d categories, now processing in hierarchy order...", len(categories_to_process))
            
            # Sort categories to process parents before children when possible
            def get_depth_level(cat_id, depth=0):
                """Calculate category depth to sort parents first"""
                if depth > 10:  # Prevent infinite recursion
                    return depth
                if cat_id not in categories_data:
                    return depth
                parent_id = categories_data[cat_id].get('parent_id')
                if not parent_id or parent_id in ['0', '1', '2']:
                    return depth
                if parent_id in categories_data:
                    return get_depth_level(parent_id, depth + 1)
                return depth
            
            # Sort by depth (parents first)
            categories_to_process.sort(key=lambda x: get_depth_level(x))
            
            # Process categories in order
            for i, category_id in enumerate(categories_to_process):
                cat_data = categories_data[category_id]
                
                # Early exit if too many errors
                if error_count > 5:
                    _logger.warning("Too many errors (%d), stopping category import", error_count)
                    break
                
                try:
                    name_text = cat_data['name']
                    parent_id = cat_data['parent_id']
                    
                    # Check if category already exists
                    existing_category = category_model.search([
                        ('name', '=', name_text),
                    ], limit=1)
                    
                    if not existing_category:
                        def create_category():
                            category_vals = {
                                'name': name_text.strip(),
                            }
                            
                            # Handle parent category hierarchy
                            if parent_id and parent_id != '0' and parent_id not in ['1', '2']:
                                try:
                                    # First try to find existing parent by getting its data from Prestashop
                                    parent_detail_url = f"{test_url}/categories/{parent_id}?ws_key={self.api_key}"
                                    parent_response = session.get(parent_detail_url, timeout=10)
                                    
                                    if parent_response.status_code == 200:
                                        parent_root = ET.fromstring(parent_response.content)
                                        parent_element = parent_root.find('.//category')
                                        
                                        if parent_element is not None:
                                            parent_name_elem = parent_element.find('.//name/language')
                                            if parent_name_elem is None:
                                                parent_name_elem = parent_element.find('name')
                                            
                                            if parent_name_elem is not None:
                                                parent_name = parent_name_elem.text.strip()
                                                
                                                # Search for existing parent category
                                                parent_category = category_model.search([
                                                    ('name', '=', parent_name)
                                                ], limit=1)
                                                
                                                if parent_category:
                                                    category_vals['parent_id'] = parent_category.id
                                                    _logger.debug("🔗 Found parent category: %s for %s", parent_name, name_text)
                                                else:
                                                    # Parent doesn't exist, create it first
                                                    parent_vals = {'name': parent_name}
                                                    parent_category = category_model.create(parent_vals)
                                                    category_vals['parent_id'] = parent_category.id
                                                    _logger.info("✅ Created parent category: %s (for %s)", parent_name, name_text)
                                except Exception as parent_error:
                                    _logger.warning("⚠️ Could not process parent category %s for %s: %s", parent_id, name_text, str(parent_error))
                            
                            return category_model.create(category_vals)
                        
                        try:
                            category_obj = self._safe_database_operation(create_category, f"category creation {category_id}")
                            imported_count += 1
                            
                            hierarchy_info = ""
                            if category_obj.parent_id:
                                hierarchy_info = f" (under {category_obj.parent_id.name})"
                            
                            _logger.info("✅ Created category: %s%s (Prestashop ID: %s)", 
                                       category_obj.name, hierarchy_info, category_id)
                        except Exception as create_error:
                            _logger.error("Error creating category %s: %s", category_id, str(create_error))
                            error_count += 1
                    else:
                        skipped_count += 1
                        _logger.debug("Category already exists: %s", name_text)
                                        
                except Exception as e:
                    error_count += 1
                    _logger.error("Error processing category %s: %s", category_id, str(e))
                
                # Progress logging every 3 categories
                if (i + 1) % 3 == 0:
                    self._log_import_progress(i + 1, len(categories_to_process), imported_count, skipped_count, error_count, "category")
                
                # Shorter delay to speed up processing
                time.sleep(0.1)
            
            # Final report with detailed error information
            if error_count > 0:
                return self._create_error_report(
                    "⚠️ Category Import Completed with ERRORS!",
                    f"Import process completed but encountered {error_count} errors",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Common issues: Connection timeouts, invalid XML, or API permissions."
                )
            elif imported_count == 0:
                return self._create_error_report(
                    "⚠️ No Categories Imported!",
                    "No new categories were created during import",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Possible issues: All categories already exist, connection problems, or no valid categories found."
                )
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '✅ Category Import Successful!',
                        'message': f'Import completed successfully!\n\n• Imported: {imported_count} new categories\n• Skipped: {skipped_count} (already exist or root categories)',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            
        except Exception as e:
            _logger.error("Category import failed: %s", str(e))
            return self._create_error_report(
                "💥 CRITICAL ERROR - Category Import Failed!",
                f"Import process crashed with error: {str(e)}",
                context="""CRITICAL ERROR SOLUTIONS:
• Check internet connection
• Verify Prestashop URL and API key
• Test connection first
• Check server logs for technical details
• Contact system administrator if problem persists"""
            )

    def action_import_products(self):
        """Import products from Prestashop with enhanced error handling and connection management"""
        self.ensure_one()
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        # Create a session for connection reuse
        session = requests.Session()
        session.headers.update({'User-Agent': 'Odoo-Prestashop-Importer/1.0'})
        
        # Disable mail tracking and computed fields during import to prevent transaction errors
        context = dict(self.env.context)
        context.update({
            'tracking_disable': True,
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'mail_auto_subscribe_no_notify': True,
            'import_mode': True,  # Signal that we're in import mode
        })
        
        imported_count = 0
        skipped_count = 0
        error_count = 0
        
        try:
            # Get products from Prestashop with optimized timeout
            products_url = f"{test_url}/products?ws_key={self.api_key}&limit=30"
            _logger.info("🚀 Starting product import from: %s", products_url)
            
            try:
                response = session.get(products_url, timeout=30)  # Reduced to 30 seconds
                _logger.info("✅ Successfully got product list in %.2f seconds", response.elapsed.total_seconds())
            except requests.exceptions.Timeout:
                return self._create_error_report(
                    "❌ TIMEOUT ERROR - Product Import Failed",
                    "Connection timeout while getting product list (>30 seconds)",
                    context="""TIMEOUT SOLUTIONS:
• Your Prestashop server is too slow (>30s for product list)
• Try importing during off-peak hours (night/weekend)
• Contact your hosting provider about server performance
• Check if other plugins are slowing down your server
• Consider upgrading your hosting plan
• Check server logs for PHP memory/execution time limits"""
                )
            except requests.exceptions.ConnectionError:
                return self._create_error_report(
                    "❌ CONNECTION ERROR - Product Import Failed",
                    "Cannot connect to Prestashop server",
                    context="""CONNECTION SOLUTIONS:
• Check your internet connection
• Verify Prestashop URL is correct and accessible
• Check if Prestashop server is running
• Verify firewall/security settings
• Test the URL manually in a browser
• Check if server is behind a firewall or CDN"""
                )
            
            if response.status_code != 200:
                return self._create_error_report(
                    "❌ HTTP ERROR - Product Import Failed",
                    f"Prestashop API returned HTTP {response.status_code}",
                    context=f"""HTTP ERROR DETAILS:
Status Code: {response.status_code}
Response: {response.text[:500]}...

COMMON HTTP ERRORS:
• 401 Unauthorized: Invalid API key
• 403 Forbidden: API key lacks permissions for products
• 404 Not Found: Wrong URL or API endpoint
• 500 Server Error: Prestashop server problem
• 503 Service Unavailable: Server overloaded

SOLUTIONS:
• Check API key in Prestashop admin
• Verify webservice permissions for products
• Test connection first"""
                )
            
            # Parse XML response
            try:
                root = ET.fromstring(response.content)
                products = root.findall('.//product')
            except ET.ParseError as e:
                return self._create_error_report(
                    "❌ XML PARSE ERROR - Product Import Failed",
                    f"Invalid XML response from Prestashop API: {str(e)}",
                    context=f"""XML ERROR DETAILS:
The server returned invalid XML data.

POSSIBLE CAUSES:
• Server returned HTML instead of XML (check .htaccess)
• Server error or crash during request
• API endpoint not properly configured
• Memory or timeout issues on Prestashop server

SOLUTIONS:
• Check Prestashop .htaccess file configuration
• Verify webservice is enabled in Prestashop admin
• Check server error logs
• Test API endpoint manually in browser"""
                )
            
            if not products:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '⚠️ No Products Found!',
                        'message': 'No products were found in your Prestashop store.\n\nPossible reasons:\n- Store has no products yet\n- API permissions are limited\n- Connection or server issues\n\nCheck your Prestashop admin panel to verify products exist.',
                        'type': 'warning',
                        'sticky': True,
                    }
                }
            
            _logger.info("Found %d products to process", len(products))
            
            product_model = self.env['product.template'].with_context(context)
            category_model = self.env['product.category'].with_context(context)
            
            for i, product in enumerate(products):
                product_id = product.get('id')
                if not product_id:
                    skipped_count += 1
                    continue
                
                # Early exit if too many consecutive errors
                if error_count > 10 and (error_count / max(i + 1, 1)) > 0.3:
                    _logger.error("🚨 Too many errors (%d/%d = %.1f%%), stopping import", error_count, i + 1, (error_count / (i + 1) * 100))
                    return self._create_error_report(
                        "❌ IMPORT STOPPED - Too Many Errors!",
                        f"Import stopped after {i + 1} products due to high error rate ({error_count} errors)",
                        imported=imported_count,
                        skipped=skipped_count,
                        errors=error_count,
                        context="""HIGH ERROR RATE SOLUTIONS:
• Your Prestashop server may be overloaded or misconfigured
• Try importing during off-peak hours
• Check server resources (CPU, memory, database)
• Verify API permissions are correct for products
• Test connection stability first
• Contact hosting provider about server performance"""
                    )
                
                try:
                    # Get detailed product data with improved retry logic and session reuse
                    product_detail_url = f"{test_url}/products/{product_id}?ws_key={self.api_key}"
                    detail_response = None
                    
                    for attempt in range(3):  # Retry up to 3 times
                        try:
                            # Use session for connection reuse
                            detail_response = session.get(product_detail_url, timeout=15)  # Reduced timeout
                            break
                        except requests.exceptions.Timeout:
                            _logger.warning("⏱️ Timeout getting product %s (attempt %d/3)", product_id, attempt + 1)
                            if attempt == 2:  # Last attempt
                                error_count += 1
                                break
                            time.sleep(2)  # Shorter wait between retries
                        except requests.exceptions.ConnectionError:
                            _logger.warning("🔌 Connection error getting product %s (attempt %d/3)", product_id, attempt + 1)
                            if attempt == 2:
                                error_count += 1
                                break
                            time.sleep(3)  # Wait before retry on connection error
                        except Exception as e:
                            _logger.warning("❌ Unexpected error getting product %s: %s", product_id, str(e))
                            if attempt == 2:
                                error_count += 1
                                break
                            time.sleep(2)
                    
                    if detail_response is None:
                        continue  # Skip this product if all retries failed
                
                    if detail_response.status_code == 200:
                        try:
                            detail_root = ET.fromstring(detail_response.content)
                            product_element = detail_root.find('.//product')
                            
                            if product_element is not None:
                                # Extract product data with comprehensive parsing
                                name_elem = product_element.find('.//name/language')
                                if name_elem is None:
                                    name_elem = product_element.find('name')
                                
                                price_elem = product_element.find('price')
                                reference_elem = product_element.find('reference')
                                active_elem = product_element.find('active')
                                description_elem = product_element.find('.//description/language')
                                if description_elem is None:
                                    description_elem = product_element.find('description')
                                description_short_elem = product_element.find('.//description_short/language')
                                if description_short_elem is None:
                                    description_short_elem = product_element.find('description_short')
                                
                                # Extract categories
                                categories_elem = product_element.find('associations/categories')
                                category_ids = []
                                if categories_elem is not None:
                                    for cat in categories_elem.findall('category'):
                                        cat_id = cat.find('id')
                                        if cat_id is not None and cat_id.text:
                                            category_ids.append(cat_id.text)
                                
                                # Extract images
                                images_elem = product_element.find('associations/images')
                                image_ids = []
                                if images_elem is not None:
                                    for img in images_elem.findall('image'):
                                        img_id = img.find('id')
                                        if img_id is not None and img_id.text:
                                            image_ids.append(img_id.text)
                                
                                name_text = name_elem.text if name_elem is not None and name_elem.text else f'Product {product_id}'
                                price_text = price_elem.text if price_elem is not None and price_elem.text else '0'
                                reference_text = reference_elem.text if reference_elem is not None and reference_elem.text else ''
                                active_text = active_elem.text if active_elem is not None else '1'
                                description_text = description_elem.text if description_elem is not None and description_elem.text else ''
                                description_short_text = description_short_elem.text if description_short_elem is not None and description_short_elem.text else ''
                                
                                # Clean and validate name
                                name_text = name_text.strip()
                                if not name_text or name_text == '':
                                    name_text = f'Product {product_id}'
                                
                                if name_text:
                                    # Check if product already exists by reference or name
                                    domain = []
                                    if reference_text:
                                        domain.append(('default_code', '=', reference_text))
                                    else:
                                        domain.append(('name', '=', name_text))
                                    
                                    existing_product = product_model.search(domain, limit=1)
                                    
                                    if not existing_product:
                                        try:
                                            # Process categories for this product
                                            odoo_category_ids = []
                                            if category_ids:
                                                odoo_category_ids = self._get_or_create_categories(category_ids, session, test_url)
                                            
                                            def create_product():
                                                product_vals = {
                                                    'name': name_text,
                                                    'type': 'consu',  # Changed from 'product' to 'consu' for Odoo 18
                                                    'sale_ok': True,
                                                    'purchase_ok': True,
                                                    'default_code': reference_text or f'PS_{product_id}',
                                                    'active': active_text == '1',
                                                    'description': description_text[:2000] if description_text else '',  # Limit description length
                                                    'description_sale': description_short_text[:1000] if description_short_text else '',
                                                }
                                                
                                                # Assign categories
                                                if odoo_category_ids:
                                                    # Use the first category as main category, others as additional
                                                    product_vals['categ_id'] = odoo_category_ids[0]
                                                    if len(odoo_category_ids) > 1:
                                                        product_vals['public_categ_ids'] = [(6, 0, odoo_category_ids)]
                                                
                                                # Set price with validation
                                                try:
                                                    price_value = float(price_text) if price_text else 0.0
                                                    product_vals['list_price'] = max(0.0, price_value)
                                                except (ValueError, TypeError):
                                                    product_vals['list_price'] = 0.0
                                                    _logger.warning("Invalid price for product %s: %s", product_id, price_text)
                                                
                                                return product_model.create(product_vals)
                                            
                                            product_obj = self._safe_database_operation(create_product, f"product creation {product_id}")
                                            
                                            # Import product images with transaction safety
                                            if image_ids:
                                                try:
                                                    self._import_product_images(product_obj, product_id, image_ids, session, test_url)
                                                except Exception as img_error:
                                                    _logger.warning("⚠️ Failed to import images for product %s: %s", product_id, str(img_error))
                                                    # Continue with product creation even if images fail
                                            
                                            imported_count += 1
                                            _logger.info("✅ Created product: %s (Prestashop ID: %s) with %d categories and %d images", 
                                                       product_obj.name, product_id, len(odoo_category_ids), len(image_ids))
                                        except Exception as create_error:
                                            error_count += 1
                                            _logger.error("❌ Failed to create product %s: %s", product_id, str(create_error))
                                    else:
                                        skipped_count += 1
                                        _logger.debug("Product already exists: %s", name_text)
                                else:
                                    error_count += 1
                                    _logger.warning("Product %s has no valid name", product_id)
                            else:
                                error_count += 1
                                _logger.warning("No product data found for ID %s", product_id)
                        except ET.ParseError as xml_error:
                            error_count += 1
                            _logger.warning("Invalid XML for product %s: %s", product_id, str(xml_error))
                    else:
                        error_count += 1
                        _logger.warning("Failed to get product %s: HTTP %s", product_id, detail_response.status_code)
                
                except Exception as e:
                    error_count += 1
                    _logger.error("💥 Error processing product %s: %s", product_id, str(e))
                
                # More frequent progress logging and connection monitoring
                if (i + 1) % 3 == 0:  # Every 3 products instead of 5
                    self._log_import_progress(i + 1, len(products), imported_count, skipped_count, error_count, "product")
                    
                    # Check if we should pause due to errors
                    if error_count > 0 and (error_count / (i + 1)) > 0.2:  # >20% error rate
                        _logger.warning("⚠️ High error rate detected, adding longer pause...")
                        time.sleep(2)  # Longer pause when many errors
                
                # Shorter delay for normal operation, longer for errors
                if error_count > 0 and (error_count / max(i + 1, 1)) > 0.1:
                    time.sleep(1.0)  # 1 second delay when errors detected
                else:
                    time.sleep(0.3)  # Shorter delay for normal operation
                
                # Progress logging every 10 products (removed manual commits to prevent transaction errors)
                if (i + 1) % 10 == 0:
                    _logger.info("💾 Processed %d products so far", i + 1)
            
            # Final report with detailed error information
            if error_count > 0:
                return self._create_error_report(
                    "⚠️ Product Import Completed with ERRORS!",
                    f"Import process completed but encountered {error_count} errors",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="""COMMON PRODUCT IMPORT ISSUES:
• Missing or invalid product names
• Price format errors (non-numeric values)
• Empty or null product data from Prestashop
• Server timeout or connection issues
• API permissions missing for products
• XML parsing errors from malformed responses
• Product type compatibility (Odoo 18 uses 'consu' for goods instead of 'product')

SOLUTIONS:
• Check product data quality in Prestashop admin
• Verify all products have valid names and prices
• Test connection stability first
• Check server logs for detailed errors
• Ensure API key has full product permissions
• Update module for Odoo version compatibility"""
                )
            elif imported_count == 0:
                return self._create_error_report(
                    "⚠️ No Products Imported!",
                    "No new products were created during import",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="""POSSIBLE CAUSES:
• All products already exist in Odoo
• No valid products found in Prestashop
• Connection or API permission issues
• Server problems preventing product access
• Product data quality issues (missing names/prices)

TROUBLESHOOTING:
• Check if products exist in Prestashop admin
• Verify API key has product permissions
• Test connection first
• Check server logs for errors"""
                )
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '✅ Product Import Successful!',
                        'message': f'Import completed successfully!\n\n• Imported: {imported_count} new products\n• Skipped: {skipped_count} (already exist)',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            
        except Exception as e:
            _logger.error("Product import failed: %s", str(e))
            return self._create_error_report(
                "💥 CRITICAL ERROR - Product Import Failed!",
                f"Import process crashed with error: {str(e)}",
                context="""CRITICAL ERROR SOLUTIONS:
• Check internet connection
• Verify Prestashop URL and API key
• Test connection first
• Check server logs for technical details
• Contact system administrator if problem persists"""
            )

    def action_import_customer_groups(self):
        """Import customer groups as pricelists from Prestashop with detailed error handling"""
        self.ensure_one()
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        try:
            # Get customer groups from Prestashop using direct HTTP call
            groups_url = f"{test_url}/groups?ws_key={self.api_key}"
            _logger.info("Starting customer groups import from: %s", groups_url)
            
            try:
                response = requests.get(groups_url, timeout=90)
            except requests.exceptions.Timeout:
                return self._create_error_report(
                    "❌ TIMEOUT ERROR - Customer Groups Import Failed",
                    "Connection timeout while getting customer groups (>90 seconds)",
                    context="""TIMEOUT SOLUTIONS:
• Your Prestashop server is too slow or overloaded
• Try importing during off-peak hours (night/weekend)  
• Contact your hosting provider about server performance
• Check if other plugins are slowing down your server
• Consider upgrading your hosting plan"""
                )
            except requests.exceptions.ConnectionError:
                return self._create_error_report(
                    "❌ CONNECTION ERROR - Customer Groups Import Failed",
                    "Cannot connect to Prestashop server",
                    context="""CONNECTION SOLUTIONS:
• Check your internet connection
• Verify Prestashop URL is correct and accessible
• Check if Prestashop server is running
• Verify firewall/security settings
• Test the URL manually in a browser"""
                )
            
            if response.status_code != 200:
                return self._create_error_report(
                    "❌ HTTP ERROR - Customer Groups Import Failed",
                    f"Prestashop API returned HTTP {response.status_code}",
                    context=f"""HTTP ERROR DETAILS:
Status Code: {response.status_code}
Response: {response.text[:500]}...

COMMON HTTP ERRORS:
• 401 Unauthorized: Invalid API key
• 403 Forbidden: API key lacks permissions
• 404 Not Found: Wrong URL or API endpoint  
• 500 Server Error: Prestashop server problem
• 503 Service Unavailable: Server overloaded

SOLUTIONS:
• Check API key in Prestashop admin
• Verify webservice permissions for groups
• Test connection first"""
                )
            
            # Parse XML response
            try:
                root = ET.fromstring(response.content)
                groups = root.findall('.//group')
            except ET.ParseError as e:
                return self._create_error_report(
                    "❌ XML PARSE ERROR - Customer Groups Import Failed",
                    f"Invalid XML response from Prestashop API: {str(e)}",
                    context=f"""XML ERROR DETAILS:
The server returned invalid XML data.

POSSIBLE CAUSES:
• Server returned HTML instead of XML (check .htaccess)
• Server error or crash during request
• API endpoint not properly configured
• Memory or timeout issues on Prestashop server

SOLUTIONS:
• Check Prestashop .htaccess file configuration
• Verify webservice is enabled in Prestashop admin
• Check server error logs
• Test API endpoint manually in browser"""
                )
            
            if not groups:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '⚠️ No Customer Groups Found!',
                        'message': 'No customer groups were found in your Prestashop store.\n\nPossible reasons:\n- Store has no custom customer groups\n- API permissions are limited\n- Connection or server issues\n\nCheck your Prestashop admin panel to verify groups exist.',
                        'type': 'warning',
                        'sticky': True,
                    }
                }
            
            imported_count = 0
            skipped_count = 0
            error_count = 0
            pricelist_model = self.env['product.pricelist']
            
            _logger.info("Found %d customer groups to process", len(groups))
            
            for i, group in enumerate(groups):
                group_id = group.get('id')
                if not group_id:
                    skipped_count += 1
                    continue
                
                try:
                    # Get detailed group data with retry logic
                    group_detail_url = f"{test_url}/groups/{group_id}?ws_key={self.api_key}"
                    
                    for attempt in range(3):  # Retry up to 3 times
                        try:
                            detail_response = requests.get(group_detail_url, timeout=60)
                            break
                        except requests.exceptions.Timeout:
                            if attempt == 2:  # Last attempt
                                _logger.warning("Timeout getting group %s after 3 attempts", group_id)
                                error_count += 1
                                break
                            time.sleep(5)  # Wait 5 seconds before retry
                        except requests.exceptions.ConnectionError:
                            _logger.warning("Connection error getting group %s", group_id)
                            error_count += 1
                            break
                    else:
                        continue  # Skip this group if all retries failed
                    
                    if detail_response.status_code == 200:
                        try:
                            detail_root = ET.fromstring(detail_response.content)
                            group_element = detail_root.find('.//group')
                            
                            if group_element is not None:
                                # Extract group data
                                name_elem = group_element.find('.//name/language')
                                if name_elem is None:
                                    name_elem = group_element.find('name')
                                
                                reduction_elem = group_element.find('reduction')
                                
                                name_text = name_elem.text if name_elem is not None else f'Group {group_id}'
                                reduction_text = reduction_elem.text if reduction_elem is not None else '0'
                                
                                if name_text:
                                    # Check if pricelist already exists
                                    existing_pricelist = pricelist_model.search([
                                        ('name', '=', f"Prestashop - {name_text}"),
                                    ], limit=1)
                                    
                                    if not existing_pricelist:
                                        # Create pricelist based on customer group
                                        pricelist_vals = {
                                            'name': f"Prestashop - {name_text}",
                                            'active': True,
                                            'company_id': self.company_id.id,
                                            'currency_id': self.env.company.currency_id.id,
                                        }
                                        
                                        # Get discount rate if available
                                        try:
                                            discount = float(reduction_text) if reduction_text else 0
                                        except (ValueError, TypeError):
                                            discount = 0
                                        
                                        if discount > 0:
                                            pricelist_vals['item_ids'] = [(0, 0, {
                                                'applied_on': '3_global',
                                                'compute_price': 'percentage',
                                                'percent_price': discount,
                                            })]
                                        
                                        pricelist = pricelist_model.create(pricelist_vals)
                                        imported_count += 1
                                        _logger.info("Created pricelist: %s (Prestashop Group ID: %s)", pricelist.name, group_id)
                                    else:
                                        skipped_count += 1
                                        _logger.debug("Pricelist already exists: %s", name_text)
                                else:
                                    skipped_count += 1
                                    _logger.warning("Group %s has no name", group_id)
                            else:
                                error_count += 1
                                _logger.warning("No group data found for ID %s", group_id)
                        except ET.ParseError:
                            error_count += 1
                            _logger.warning("Invalid XML for group %s", group_id)
                    else:
                        error_count += 1
                        _logger.warning("Failed to get group %s: HTTP %s", group_id, detail_response.status_code)
                
                except Exception as e:
                    error_count += 1
                    _logger.error("Error processing group %s: %s", group_id, str(e))
                
                # Progress logging every 3 groups (they're usually fewer than customers)
                if (i + 1) % 3 == 0:
                    self._log_import_progress(i + 1, len(groups), imported_count, skipped_count, error_count, "customer group")
                
                # Small delay to reduce server load
                time.sleep(0.3)
            
            # Final report with detailed error information
            if error_count > 0:
                return self._create_error_report(
                    "⚠️ Customer Groups Import Completed with ERRORS!",
                    f"Import process completed but encountered {error_count} errors",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Check server logs for detailed error information."
                )
            elif imported_count == 0:
                return self._create_error_report(
                    "⚠️ No Customer Groups Imported!",
                    "No new pricelists were created during import",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Possible issues: All groups already exist as pricelists, server problems, or API permissions."
                )
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '✅ Customer Groups Import Successful!',
                        'message': f'Import completed successfully!\n\n• Imported: {imported_count} new pricelists\n• Skipped: {skipped_count} (already exist)',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            
        except Exception as e:
            _logger.error("Customer groups import failed: %s", str(e))
            return self._create_error_report(
                "💥 CRITICAL ERROR - Customer Groups Import Failed!",
                f"Import process crashed with error: {str(e)}",
                context="""CRITICAL ERROR SOLUTIONS:
• Check internet connection
• Verify Prestashop URL and API key
• Test connection first
• Check server logs for technical details
• Contact system administrator if problem persists"""
            )

    def _get_or_create_categories(self, category_ids, session, test_url):
        """Get or create Odoo categories from Prestashop category IDs with hierarchy support"""
        if not category_ids:
            return []
        
        category_model = self.env['product.category']
        odoo_category_ids = []
        
        # Dictionary to store category data and hierarchy
        categories_data = {}
        
        _logger.info("🏷️ Processing %d categories for product", len(category_ids))
        
        # First pass: Get all category data from Prestashop
        for cat_id in category_ids:
            if cat_id in ['1', '2']:  # Skip root categories
                continue
                
            try:
                # Get category details from Prestashop
                category_detail_url = f"{test_url}/categories/{cat_id}?ws_key={self.api_key}"
                response = session.get(category_detail_url, timeout=10)
                
                if response.status_code == 200:
                    detail_root = ET.fromstring(response.content)
                    category_element = detail_root.find('.//category')
                    
                    if category_element is not None:
                        # Extract category data
                        name_elem = category_element.find('.//name/language')
                        if name_elem is None:
                            name_elem = category_element.find('name')
                        
                        parent_elem = category_element.find('id_parent')
                        
                        name_text = name_elem.text if name_elem is not None else f'Category {cat_id}'
                        parent_id = parent_elem.text if parent_elem is not None else None
                        
                        # Store category data
                        categories_data[cat_id] = {
                            'name': name_text.strip(),
                            'parent_id': parent_id,
                            'prestashop_id': cat_id
                        }
                        
                        _logger.debug("📁 Found category: %s (Parent: %s)", name_text, parent_id)
                    else:
                        _logger.warning("No category data found for ID %s", cat_id)
                else:
                    _logger.warning("Failed to get category %s: HTTP %s", cat_id, response.status_code)
                    
            except Exception as e:
                _logger.error("Error getting category %s data: %s", cat_id, str(e))
                
        # Second pass: Create categories with proper hierarchy
        created_categories = {}
        
        def create_category_with_parent(cat_id, cat_data):
            """Recursively create category and its parent if needed"""
            if cat_id in created_categories:
                return created_categories[cat_id]
            
            # Check if category already exists in Odoo
            existing_category = category_model.search([
                ('name', '=', cat_data['name'])
            ], limit=1)
            
            if existing_category:
                created_categories[cat_id] = existing_category.id
                _logger.debug("✅ Category exists: %s", cat_data['name'])
                return existing_category.id
            
            # Prepare category values
            category_vals = {
                'name': cat_data['name'],
            }
            
            # Handle parent category
            parent_id = cat_data.get('parent_id')
            if parent_id and parent_id != '0' and parent_id not in ['1', '2']:
                # Check if parent exists in our data
                if parent_id in categories_data:
                    # Recursively create parent first
                    parent_odoo_id = create_category_with_parent(parent_id, categories_data[parent_id])
                    if parent_odoo_id:
                        category_vals['parent_id'] = parent_odoo_id
                        _logger.debug("🔗 Setting parent for %s: %s", cat_data['name'], categories_data[parent_id]['name'])
                else:
                    # Try to find parent by name or create a basic one
                    parent_category = category_model.search([
                        ('name', 'ilike', f'%{parent_id}%')
                    ], limit=1)
                    if parent_category:
                        category_vals['parent_id'] = parent_category.id
            
            try:
                # Create the category
                category_obj = category_model.create(category_vals)
                created_categories[cat_id] = category_obj.id
                
                hierarchy_info = ""
                if 'parent_id' in category_vals:
                    parent_name = category_model.browse(category_vals['parent_id']).name
                    hierarchy_info = f" (under {parent_name})"
                
                _logger.info("✅ Created category: %s%s (Prestashop ID: %s)", 
                           category_obj.name, hierarchy_info, cat_id)
                
                return category_obj.id
                
            except Exception as create_error:
                _logger.error("❌ Failed to create category %s: %s", cat_data['name'], str(create_error))
                return None
        
        # Create all categories with hierarchy
        for cat_id, cat_data in categories_data.items():
            odoo_id = create_category_with_parent(cat_id, cat_data)
            if odoo_id:
                odoo_category_ids.append(odoo_id)
        
        _logger.info("🎯 Successfully processed %d categories for product assignment", len(odoo_category_ids))
        return odoo_category_ids

    def _import_product_images(self, product_obj, product_id, image_ids, session, test_url):
        """Import product images from Prestashop"""
        if not image_ids:
            return
        
        _logger.info("🖼️ Processing %d images for product %s", len(image_ids), product_obj.name)
        
        imported_count = 0
        error_count = 0
        
        # Process each image
        for i, img_id in enumerate(image_ids):
            try:
                # Get image URL from Prestashop API
                # Prestashop 1.6 image URL format: /api/images/products/{product_id}/{image_id}
                image_url = f"{test_url}/images/products/{product_id}/{img_id}?ws_key={self.api_key}"
                
                _logger.debug("📥 Downloading image %s from: %s", img_id, image_url)
                
                # Download the image
                response = session.get(image_url, timeout=30)
                
                if response.status_code == 200:
                    # Validate that we got actual image data
                    content_type = response.headers.get('content-type', '').lower()
                    if any(img_type in content_type for img_type in ['image/', 'jpeg', 'png', 'gif', 'webp']):
                        
                        # Convert image to base64
                        import base64
                        image_data = base64.b64encode(response.content).decode('utf-8')
                        
                        try:
                            if i == 0:
                                # First image becomes the main product image
                                product_obj.write({'image_1920': image_data})
                                _logger.info("✅ Set main image for product %s (Prestashop Image ID: %s)", 
                                           product_obj.name, img_id)
                                imported_count += 1
                            else:
                                # Additional images go to product images
                                # Note: In Odoo 18, we can use product.image model for additional images
                                if hasattr(self.env, 'product.image'):
                                    self.env['product.image'].create({
                                        'name': f'Prestashop Image {img_id}',
                                        'image_1920': image_data,
                                        'product_tmpl_id': product_obj.id,
                                    })
                                    _logger.info("✅ Added additional image for product %s (Prestashop Image ID: %s)", 
                                               product_obj.name, img_id)
                                    imported_count += 1
                                else:
                                    # Fallback: just set as main image if product.image not available
                                    if not product_obj.image_1920:
                                        product_obj.write({'image_1920': image_data})
                                        imported_count += 1
                                        
                        except Exception as save_error:
                            error_count += 1
                            _logger.error("❌ Failed to save image %s for product %s: %s", 
                                        img_id, product_obj.name, str(save_error))
                    else:
                        error_count += 1
                        _logger.warning("⚠️ Invalid image format for image %s (Content-Type: %s)", 
                                      img_id, content_type)
                else:
                    error_count += 1
                    _logger.warning("❌ Failed to download image %s: HTTP %s", img_id, response.status_code)
                    
            except Exception as e:
                error_count += 1
                _logger.error("💥 Error processing image %s for product %s: %s", 
                            img_id, product_obj.name, str(e))
            
            # Small delay between image downloads to avoid overwhelming the server
            if i < len(image_ids) - 1:  # Don't delay after the last image
                time.sleep(0.5)
        
        if imported_count > 0:
            _logger.info("🎉 Successfully imported %d images for product %s", imported_count, product_obj.name)
        if error_count > 0:
            _logger.warning("⚠️ Failed to import %d images for product %s", error_count, product_obj.name)
