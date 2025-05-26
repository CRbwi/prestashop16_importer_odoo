# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
import requests
import xml.etree.ElementTree as ET
import time

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

    def _create_error_report(self, title, main_error, imported=0, skipped=0, errors=0, context=""):
        """Helper method to create detailed error notifications"""
        if errors > 0 or imported == 0:
            # Create detailed error message
            error_details = f"""IMPORT SUMMARY:
• Imported: {imported}
• Skipped: {skipped} 
• Errors: {errors}

ERROR DETAILS:
{main_error}

{context}

TROUBLESHOOTING STEPS:
1. Test connection first (use 'Test Connection' button)
2. Check your internet connection
3. Verify Prestashop server is running
4. Check API key permissions in Prestashop admin
5. Look at Odoo server logs for technical details
6. Try importing during off-peak hours if server is slow"""

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': title,
                    'message': error_details,
                    'type': 'danger',
                    'sticky': True,  # Keep error notifications visible
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
        """Import customers from Prestashop with detailed error handling"""
        self.ensure_one()
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        try:
            # Get customers list - NO LIMIT to get ALL customers
            customers_url = f"{test_url}/customers?ws_key={self.api_key}"
            _logger.info("Starting customer import from: %s", customers_url)
            
            try:
                response = requests.get(customers_url, timeout=90)
            except requests.exceptions.Timeout:
                return self._create_error_report(
                    "❌ TIMEOUT ERROR - Customer Import Failed",
                    "Connection timeout while getting customer list (>90 seconds)",
                    context="""TIMEOUT SOLUTIONS:
• Your Prestashop server is too slow or overloaded
• Try importing during off-peak hours (night/weekend)
• Contact your hosting provider about server performance
• Check if other plugins are slowing down your server
• Consider upgrading your hosting plan"""
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
• Test the URL manually in a browser"""
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
            partner_model = self.env['res.partner']
            
            _logger.info("Found %d customers to process", len(customers))
            
            for i, customer in enumerate(customers):
                customer_id = customer.get('id')
                if not customer_id:
                    skipped_count += 1
                    continue
                
                try:
                    # Get detailed customer data with retry logic
                    customer_detail_url = f"{test_url}/customers/{customer_id}?ws_key={self.api_key}"
                    
                    for attempt in range(3):  # Retry up to 3 times
                        try:
                            detail_response = requests.get(customer_detail_url, timeout=60)
                            break
                        except requests.exceptions.Timeout:
                            if attempt == 2:  # Last attempt
                                _logger.warning("Timeout getting customer %s after 3 attempts", customer_id)
                                error_count += 1
                                break
                            time.sleep(5)  # Wait 5 seconds before retry
                        except requests.exceptions.ConnectionError:
                            _logger.warning("Connection error getting customer %s", customer_id)
                            error_count += 1
                            break
                    else:
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
                                        # Create customer
                                        partner_vals = {
                                            'name': f"{firstname_text} {lastname_text}".strip() or email_text,
                                            'email': email_text,
                                            'is_company': False,
                                            'customer_rank': 1,
                                            'comment': f"Imported from Prestashop (ID: {customer_id})",
                                        }
                                        
                                        partner = partner_model.create(partner_vals)
                                        imported_count += 1
                                        _logger.info("Created customer: %s (Prestashop ID: %s)", partner.name, customer_id)
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
                    _logger.error("Error processing customer %s: %s", customer_id, str(e))
                
                # Progress logging every 5 customers
                if (i + 1) % 5 == 0:
                    self._log_import_progress(i + 1, len(customers), imported_count, skipped_count, error_count, "customer")
                
                # Small delay to reduce server load
                time.sleep(0.5)
            
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
        """Import categories from Prestashop with detailed error handling"""
        self.ensure_one()
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        imported_count = 0
        skipped_count = 0
        error_count = 0
        
        try:
            # Get categories from Prestashop with reduced timeout and limit
            categories_url = f"{test_url}/categories?ws_key={self.api_key}&limit=20"
            _logger.info("Starting category import from: %s", categories_url)
            
            try:
                response = requests.get(categories_url, timeout=30)
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
            
            category_model = self.env['product.category']
            
            for i, category in enumerate(categories):
                category_id = category.get('id')
                if not category_id or category_id in ['1', '2']:  # Skip root categories
                    skipped_count += 1
                    continue
                
                # Early exit if too many errors
                if error_count > 5:
                    _logger.warning("Too many errors (%d), stopping category import", error_count)
                    break
                
                try:
                    # Get detailed category data with reduced timeout
                    category_detail_url = f"{test_url}/categories/{category_id}?ws_key={self.api_key}"
                    
                    # Reduced retries for faster processing
                    for attempt in range(2):  # Only 2 attempts instead of 3
                        try:
                            detail_response = requests.get(category_detail_url, timeout=15)
                            break
                        except requests.exceptions.Timeout:
                            if attempt == 1:
                                _logger.warning("Timeout getting category %s after 2 attempts", category_id)
                                error_count += 1
                                break
                            time.sleep(2)
                        except requests.exceptions.ConnectionError:
                            _logger.warning("Connection error getting category %s", category_id)
                            error_count += 1
                            break
                    else:
                        continue
                    
                    if detail_response.status_code == 200:
                        try:
                            detail_root = ET.fromstring(detail_response.content)
                            category_element = detail_root.find('.//category')
                            
                            if category_element is not None:
                                # Extract category data
                                name_elem = category_element.find('.//name/language')
                                if name_elem is None:
                                    name_elem = category_element.find('name')
                                
                                name_text = name_elem.text if name_elem is not None else f'Category {category_id}'
                                
                                if name_text and name_text.strip():
                                    # Check if category already exists
                                    existing_category = category_model.search([
                                        ('name', '=', name_text),
                                    ], limit=1)
                                    
                                    if not existing_category:
                                        try:
                                            category_vals = {
                                                'name': name_text.strip(),
                                            }
                                            
                                            category_obj = category_model.create(category_vals)
                                            imported_count += 1
                                            _logger.info("Created category: %s (Prestashop ID: %s)", category_obj.name, category_id)
                                        except Exception as create_error:
                                            _logger.error("Error creating category %s: %s", category_id, str(create_error))
                                            error_count += 1
                                    else:
                                        skipped_count += 1
                                        _logger.debug("Category already exists: %s", name_text)
                                else:
                                    error_count += 1
                                    _logger.warning("Category %s has no valid name", category_id)
                            else:
                                error_count += 1
                                _logger.warning("No category data found for ID %s", category_id)
                        except ET.ParseError:
                            error_count += 1
                            _logger.warning("Invalid XML for category %s", category_id)
                    else:
                        error_count += 1
                        _logger.warning("Failed to get category %s: HTTP %s", category_id, detail_response.status_code)
                
                except Exception as e:
                    error_count += 1
                    _logger.error("Error processing category %s: %s", category_id, str(e))
                
                # Progress logging every 3 categories
                if (i + 1) % 3 == 0:
                    self._log_import_progress(i + 1, len(categories), imported_count, skipped_count, error_count, "category")
                
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
        """Import products from Prestashop with detailed error handling"""
        self.ensure_one()
        
        # Ensure URL ends with /api
        test_url = self.prestashop_url.rstrip('/')
        if not test_url.endswith('/api'):
            test_url += '/api'
        
        imported_count = 0
        skipped_count = 0
        error_count = 0
        
        try:
            # Get products from Prestashop
            products_url = f"{test_url}/products?ws_key={self.api_key}&limit=30"
            _logger.info("Starting product import from: %s", products_url)
            
            try:
                response = requests.get(products_url, timeout=90)
            except requests.exceptions.Timeout:
                return self._create_error_report(
                    "❌ TIMEOUT ERROR - Product Import Failed",
                    "Connection timeout while getting product list (>90 seconds)",
                    context="""TIMEOUT SOLUTIONS:
• Your Prestashop server is too slow or overloaded
• Try importing during off-peak hours
• Contact your hosting provider about server performance"""
                )
            except requests.exceptions.ConnectionError:
                return self._create_error_report(
                    "❌ CONNECTION ERROR - Product Import Failed",
                    "Cannot connect to Prestashop server",
                    context="""CONNECTION SOLUTIONS:
• Check your internet connection
• Verify Prestashop URL and server status
• Test connection first"""
                )
            
            if response.status_code != 200:
                return self._create_error_report(
                    "❌ HTTP ERROR - Product Import Failed",
                    f"Failed to get products: HTTP {response.status_code}",
                    context="Check API key permissions and server status"
                )
            
            # Parse XML response
            try:
                root = ET.fromstring(response.content)
                products = root.findall('.//product')
            except ET.ParseError as e:
                return self._create_error_report(
                    "❌ XML PARSE ERROR - Product Import Failed",
                    f"Invalid XML response for products: {str(e)}",
                    context="Check server configuration and API endpoint"
                )
            
            _logger.info("Found %d products to process", len(products))
            
            product_model = self.env['product.template']
            category_model = self.env['product.category']
            
            for i, product in enumerate(products):
                product_id = product.get('id')
                if not product_id:
                    skipped_count += 1
                    continue
                
                try:
                    # Get detailed product data with timeout handling and retry
                    product_detail_url = f"{test_url}/products/{product_id}?ws_key={self.api_key}"
                    
                    for attempt in range(3):  # Retry up to 3 times
                        try:
                            detail_response = requests.get(product_detail_url, timeout=60)
                            break
                        except requests.exceptions.Timeout:
                            if attempt == 2:
                                _logger.warning("Timeout getting product %s after 3 attempts", product_id)
                                error_count += 1
                                break
                            time.sleep(5)
                        except requests.exceptions.ConnectionError:
                            _logger.warning("Connection error getting product %s", product_id)
                            error_count += 1
                            break
                    else:
                        continue
                    
                    if detail_response.status_code == 200:
                        try:
                            detail_root = ET.fromstring(detail_response.content)
                            product_element = detail_root.find('.//product')
                            
                            if product_element is not None:
                                # Extract product data
                                name_elem = product_element.find('.//name/language')
                                if name_elem is None:
                                    name_elem = product_element.find('name')
                                
                                price_elem = product_element.find('price')
                                reference_elem = product_element.find('reference')
                                
                                name_text = name_elem.text if name_elem is not None else f'Product {product_id}'
                                price_text = price_elem.text if price_elem is not None else '0'
                                reference_text = reference_elem.text if reference_elem is not None else ''
                                
                                if name_text:
                                    # Check if product already exists
                                    existing_product = product_model.search([
                                        '|',
                                        ('name', '=', name_text),
                                        ('default_code', '=', reference_text)
                                    ], limit=1)
                                    
                                    if not existing_product:
                                        product_vals = {
                                            'name': name_text,
                                            'type': 'product',
                                            'sale_ok': True,
                                            'purchase_ok': True,
                                            'default_code': reference_text,
                                        }
                                        
                                        # Set price with validation
                                        try:
                                            price_value = float(price_text) if price_text else 0.0
                                            product_vals['list_price'] = max(0.0, price_value)
                                        except (ValueError, TypeError):
                                            product_vals['list_price'] = 0.0
                                        
                                        product_obj = product_model.create(product_vals)
                                        imported_count += 1
                                        _logger.info("Created product: %s (Prestashop ID: %s)", product_obj.name, product_id)
                                    else:
                                        skipped_count += 1
                                        _logger.debug("Product already exists: %s", name_text)
                                else:
                                    error_count += 1
                                    _logger.warning("Product %s has no name", product_id)
                            else:
                                error_count += 1
                                _logger.warning("No product data found for ID %s", product_id)
                        except ET.ParseError:
                            error_count += 1
                            _logger.warning("Invalid XML for product %s", product_id)
                    else:
                        error_count += 1
                        _logger.warning("Failed to get product %s: HTTP %s", product_id, detail_response.status_code)
                
                except Exception as e:
                    error_count += 1
                    _logger.error("Error processing product %s: %s", product_id, str(e))
                
                # Progress logging every 5 products
                if (i + 1) % 5 == 0:
                    self._log_import_progress(i + 1, len(products), imported_count, skipped_count, error_count, "product")
                
                # Small delay to reduce server load
                time.sleep(0.5)
            
            # Final report with detailed error information
            if error_count > 0:
                return self._create_error_report(
                    "⚠️ Product Import Completed with ERRORS!",
                    f"Import process completed but encountered {error_count} errors",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Common issues: Missing product data, price errors, or category problems."
                )
            elif imported_count == 0:
                return self._create_error_report(
                    "⚠️ No Products Imported!",
                    "No new products were created during import",
                    imported=imported_count,
                    skipped=skipped_count,
                    errors=error_count,
                    context="Possible issues: All products already exist, connection problems, or no valid products found."
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
