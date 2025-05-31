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

    def _safe_database_operation(self, operation_func, operation_name="database operation"):
        """
        Safely execute database operations with enhanced transaction error handling
        This prevents psycopg2.errors.InFailedSqlTransaction errors specifically
        """
        max_retries = 3
        retry_delay = 1  # Initial delay in seconds
        
        for attempt in range(max_retries):
            try:
                # Execute the operation
                return operation_func()
                
            except Exception as e:
                error_message = str(e).lower()
                _logger.error("❌ Failed %s (attempt %d/%d): %s", operation_name, attempt + 1, max_retries, str(e))
                
                transaction_error_indicators = [
                    'infailedsqltransaction',
                    'transaction is aborted',
                    'current transaction is aborted',
                    'commands ignored until end of transaction',
                    'rating_mixin',
                    'mail',
                    'rating'
                ]
                
                is_transaction_error = any(indicator in error_message for indicator in transaction_error_indicators)
                
                if is_transaction_error and attempt < max_retries - 1:
                    _logger.warning("🔄 Transaction error detected in %s, attempting recovery (attempt %d/%d). Rolling back...", 
                                  operation_name, attempt + 1, max_retries)
                    try:
                        self.env.cr.rollback()
                        _logger.info("✅ Transaction rollback successful for %s after attempt %d.", operation_name, attempt + 1)
                        
                        _logger.info("Waiting for %.1f seconds before retrying %s...", retry_delay, operation_name)
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        
                        continue 
                        
                    except Exception as rollback_error:
                        _logger.error("❌ Rollback failed for %s after attempt %d: %s", 
                                      operation_name, attempt + 1, str(rollback_error))
                        _logger.error("Original error leading to rollback failure: %s", str(e))
                        raise e 
                else:
                    if attempt == max_retries - 1:
                        _logger.error("💥 All %d retry attempts exhausted for %s. Last error: %s", 
                                      max_retries, operation_name, str(e))
                    elif not is_transaction_error:
                        _logger.error("💥 Non-transaction error in %s, not retrying. Error: %s", 
                                      operation_name, str(e))
                    raise e
        
        _logger.critical("💔 Operation %s failed definitively after all attempts or due to critical unhandled error.", operation_name)
        raise Exception(f"Failed to complete {operation_name} after {max_retries} attempts or due to a non-retryable/unhandled error. Check logs for details.")

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

    def action_import_customers(self):
        """Import customers from Prestashop using background job to avoid timeout"""
        return self._start_background_import('customers')

    def _start_background_import(self, import_type):
        """Start a background import to avoid 90-second timeout issues"""
        import threading
        
        def background_wrapper():
            """Wrapper to handle background execution with proper database context"""
            with self.pool.cursor() as new_cr:
                # Create new environment with the new cursor
                new_env = api.Environment(new_cr, self.env.uid, self.env.context)
                new_self = self.with_env(new_env)
                
                try:
                    if import_type == 'customers':
                        new_self._background_customer_import()
                    elif import_type == 'categories':
                        new_self._background_category_import()
                    elif import_type == 'products':
                        new_self._background_product_import()
                    new_cr.commit()
                except Exception as e:
                    _logger.error("❌ Background import error for %s: %s", import_type, str(e))
                    new_cr.rollback()
        
        # Start background thread
        thread = threading.Thread(target=background_wrapper)
        thread.daemon = True
        thread.start()
        
        # Return immediate response to user
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'🚀 {import_type.title()} Import Started',
                'message': f'{import_type.title()} import is running in background. Check logs for progress.',
                'type': 'success',
                'sticky': False
            }
        }

    def _background_customer_import(self):
        """Background customer import with transaction safety and address support"""
        def safe_customer_import():
            """Internal function with all import logic wrapped in transaction safety"""
            test_url = self.prestashop_url.rstrip('/')
            if not test_url.endswith('/api'):
                test_url += '/api'
            
            session = requests.Session()
            session.headers.update({'User-Agent': 'Odoo-Prestashop-Importer/1.0'})
            
            context = dict(self.env.context)
            context.update({
                'tracking_disable': True, 'mail_create_nolog': True, 'mail_create_nosubscribe': True,
                'mail_auto_subscribe_no_notify': True, 'mail_auto_delete': False, 'mail_create_uid': False,
                'no_reset_password': True, 'rating_disable': True, 'no_rating': True, 'rating_skip': True,
                'compute_disable': True, 'auto_compute_disable': True, 'prefetch_disable': True,
                'import_mode': True, 'mass_import': True, 'install_mode': True, 'active_test': False,
                'no_validate': True, 'force_company': self.env.company.id, 
                'defer_parent_store_computation': True,
            })
        
            customers_url = f"{test_url}/customers?ws_key={self.api_key}"
            _logger.info("Starting transaction-safe customer import from: %s", customers_url)
            
            start_time = time.time()
            try:
                response = session.get(customers_url, timeout=300)
                elapsed_time = time.time() - start_time
                _logger.info("Successfully got customer list in %.2f seconds", elapsed_time)
                response.raise_for_status()
            except requests.exceptions.Timeout as timeout_error:
                elapsed_time = time.time() - start_time
                _logger.error("Timeout after %.2f seconds", elapsed_time)
                return self._create_error_report("❌ TIMEOUT ERROR - Customer Import Failed", f"Connection timeout while getting customer list after {elapsed_time:.1f} seconds (configured: 300s)", context="TIMEOUT SOLUTIONS:\n• Your Prestashop server is taking too long to respond...")
            except requests.exceptions.ConnectionError:
                return self._create_error_report("❌ CONNECTION ERROR - Customer Import Failed", "Cannot connect to Prestashop server", context="CONNECTION SOLUTIONS:\n• Check your internet connection...")
            except requests.exceptions.HTTPError as he:
                return self._create_error_report("❌ HTTP ERROR - Customer Import Failed", f"Prestashop API returned HTTP {he.response.status_code}", context=f"HTTP ERROR DETAILS:\nStatus Code: {he.response.status_code}\nResponse: {he.response.text[:500]}...")
            except Exception as e_list:
                 return self._create_error_report("❌ UNEXPECTED ERROR - Customer List Failed", f"An unexpected error occurred: {str(e_list)}", context="Check Odoo logs for more details.")

            # Parse XML response
            try:
                root = ET.fromstring(response.content)
                customers = root.findall('.//customer')
            except ET.ParseError as e:
                return self._create_error_report("❌ XML PARSE ERROR - Customer Import Failed", f"Invalid XML response from Prestashop API: {str(e)}", context="XML ERROR DETAILS:\nThe server returned invalid XML data...")
            
            if not customers:
                return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': '⚠️ No Customers Found!', 'message': 'No customers were found...', 'type': 'warning', 'sticky': True}}
            
            imported_count, skipped_count, error_count = 0, 0, 0
            address_imported_count, address_skipped_count, address_error_count = 0, 0, 0
            partner_model = self.env['res.partner'].with_context(context)
            total_customers = len(customers)
            _logger.info("Found %d customers to process", total_customers)
            
            for i, customer_element in enumerate(customers):
                customer_id = customer_element.get('id')
                if not customer_id:
                    _logger.warning("Skipping customer element with no ID: %s", ET.tostring(customer_element, encoding='unicode'))
                    skipped_count += 1
                    continue
                
                self._log_import_progress(i + 1, total_customers, imported_count, skipped_count, error_count, "customer")

                if error_count > 10 and (error_count / max(i + 1, 1)) > 0.3:
                    _logger.error("🚨 Too many errors (%d/%d), stopping import", error_count, i + 1)
                    return self._create_error_report("❌ IMPORT STOPPED - Too Many Errors!", f"Import stopped after {i + 1} customers due to high error rate ({error_count} errors)", imported_count, skipped_count, error_count, context="HIGH ERROR RATE SOLUTIONS:\n• Your Prestashop server may be overloaded...")
                
                detail_response = None
                customer_detail_url = f"{test_url}/customers/{customer_id}?ws_key={self.api_key}"

                try: 
                    for attempt_detail in range(3):
                        try:
                            _logger.debug("Attempt %d: Fetching details for customer %s", attempt_detail + 1, customer_id)
                            detail_response = session.get(customer_detail_url, timeout=60)
                            detail_response.raise_for_status()
                            _logger.debug("Successfully fetched customer %s details in %.2f seconds", customer_id, detail_response.elapsed.total_seconds())
                            break 
                        except requests.exceptions.Timeout:
                            _logger.warning("Timeout fetching customer %s (attempt %d/3)", customer_id, attempt_detail + 1)
                            if attempt_detail == 2: error_count += 1; _logger.error("Final timeout for customer %s.", customer_id); break
                            time.sleep(2 * (attempt_detail + 1))
                        except requests.exceptions.ConnectionError as ce:
                            _logger.warning("Connection error for customer %s (attempt %d/3): %s", customer_id, attempt_detail + 1, str(ce))
                            if attempt_detail == 2: error_count += 1; _logger.error("Final connection error for customer %s.", customer_id); break
                            time.sleep(2 * (attempt_detail + 1))
                        except requests.exceptions.HTTPError as he_detail:
                            _logger.error("HTTP error for customer %s: %s. Response: %s", customer_id, str(he_detail), he_detail.response.text[:200] if he_detail.response else "N/A")
                            error_count += 1; break 
                        except Exception as e_detail_fetch:
                            _logger.error("Unexpected error fetching customer %s (attempt %d/3): %s", customer_id, attempt_detail + 1, str(e_detail_fetch))
                            if attempt_detail == 2: error_count += 1; _logger.error("Final unexpected error fetching customer %s.", customer_id); break
                            time.sleep(2 * (attempt_detail + 1))
                    
                    if detail_response is None or detail_response.status_code != 200:
                        _logger.error("Skipping customer %s due to failed detail retrieval.", customer_id)
                        continue

                    try:
                        customer_data_xml = ET.fromstring(detail_response.content).find('customer')
                        if customer_data_xml is None:
                            _logger.error("Could not find <customer> tag in XML for customer %s", customer_id)
                            error_count += 1; continue

                        email = customer_data_xml.findtext('email')
                        firstname = customer_data_xml.findtext('firstname', '')
                        lastname = customer_data_xml.findtext('lastname', '')

                        if not email:
                            _logger.warning("Customer %s (Prestashop ID) has no email, skipping.", customer_id)
                            skipped_count += 1; continue

                        if partner_model.search_count([('email', '=ilike', email)]):
                            _logger.info("Customer %s (Email: %s) already exists. Skipping.", customer_id, email)
                            skipped_count += 1
                            
                            # Still try to import addresses for existing customers
                            existing_partner = partner_model.search([('email', '=ilike', email)], limit=1)
                            if existing_partner and not existing_partner.child_ids.filtered(lambda c: c.type in ['delivery', 'invoice']):
                                addr_imported, addr_skipped, addr_errors = self._import_customer_addresses(
                                    test_url, customer_id, existing_partner
                                )
                                address_imported_count += addr_imported
                                address_skipped_count += addr_skipped
                                address_error_count += addr_errors
                            continue
                        
                        partner_vals = {
                            'name': f"{firstname} {lastname}".strip() or email, 
                            'email': email,
                            'company_type': 'person',
                            'is_company': False,
                            'customer_rank': 1,
                            'comment': f"Imported from Prestashop (ID: {customer_id})",
                        }
                        
                        # Create customer with transaction safety
                        def create_customer():
                            return partner_model.create(partner_vals)
                        
                        new_partner = self._safe_database_operation(
                            create_customer, 
                            f"create_customer_{customer_id}"
                        )
                        
                        if new_partner:
                            _logger.info("🎉 Created new partner ID %d for Prestashop customer %s (Email: %s)", new_partner.id, customer_id, email)
                            imported_count += 1
                            
                            # Import customer addresses with transaction safety
                            def import_addresses():
                                return self._import_customer_addresses(test_url, customer_id, new_partner)
                            
                            addr_result = self._safe_database_operation(
                                import_addresses,
                                f"import_addresses_{customer_id}"
                            )
                            
                            if addr_result:
                                addr_imported, addr_skipped, addr_errors = addr_result
                                address_imported_count += addr_imported
                                address_skipped_count += addr_skipped
                                address_error_count += addr_errors

                    except ET.ParseError as e_parse_cust:
                        _logger.error("XML Parse Error for customer %s details: %s. XML: %s", customer_id, str(e_parse_cust), detail_response.text[:500])
                        error_count += 1
                    except Exception as e_process_data:
                        _logger.error("Error processing data for customer %s: %s", customer_id, str(e_process_data))
                        error_count +=1
                
                except Exception as e_outer_customer_loop: 
                    _logger.error("💥 Unexpected outer error processing Prestashop customer ID %s: %s", customer_id, str(e_outer_customer_loop))
                    error_count += 1
                
                time.sleep(0.1) 
            
            # Final report with detailed error information including addresses
            total_addresses = address_imported_count + address_skipped_count + address_error_count
            success_message = f'Import completed successfully!\n\n• Customers - Imported: {imported_count}, Skipped: {skipped_count}'
            if total_addresses > 0:
                success_message += f'\n• Addresses - Imported: {address_imported_count}, Skipped: {address_skipped_count}, Errors: {address_error_count}'
            
            title = "✅ Customer Import Successful!"
            message_parts = [f"Import process completed.", f"• Imported: {imported_count} new customers", f"• Skipped: {skipped_count} (already exist or invalid data)", f"• Errors: {error_count}"]
            if total_addresses > 0:
                message_parts.append(f"• Address Imported: {address_imported_count}, Skipped: {address_skipped_count}, Errors: {address_error_count}")
            notif_type = 'success'

            if error_count > 0 or address_error_count > 0:
                title = "⚠️ Customer Import Completed with ERRORS!"
                notif_type = 'warning'
            if imported_count == 0 and total_customers > 0 and error_count == 0 and skipped_count == total_customers:
                title = "ℹ️ Customer Import Processed - No New Customers"
                message_parts = [f"All {total_customers} customers from Prestashop already exist in Odoo or were skipped due to invalid data."]
                notif_type = 'info'
            elif imported_count == 0 and total_customers > 0 and error_count > 0 :
                 title = "❌ Customer Import Failed to Import New Records"
                 notif_type = 'danger'

            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': title, 'message': "\\n".join(message_parts), 'type': notif_type, 'sticky': error_count > 0 or address_error_count > 0}}
            
        # Execute import with database safety
        try:
            result = self._safe_database_operation(safe_customer_import, "complete customer import")
            _logger.info("🎉 BACKGROUND CUSTOMER IMPORT COMPLETED SUCCESSFULLY")
            return result
        except Exception as e:
            _logger.error("❌ BACKGROUND CUSTOMER IMPORT FAILED: %s", str(e))
            return None

    def action_import_categories(self):
        """Import categories from Prestashop using background job to avoid timeout"""
        return self._start_background_import('categories')

    def _background_category_import(self):
        """Background category import - original logic"""
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
                                    # Check if category already exists by Prestashop ID first
                                    existing_category = None
                                    if 'x_prestashop_category_id' in category_model._fields:
                                        existing_category = category_model.search([
                                            ('x_prestashop_category_id', '=', int(category_id))
                                        ], limit=1)
                                    
                                    # If not found by ID, try by name
                                    if not existing_category:
                                        existing_category = category_model.search([
                                            ('name', '=', name_text.strip()),
                                        ], limit=1)
                                    
                                    if not existing_category:
                                        try:
                                            category_vals = {
                                                'name': name_text.strip(),
                                            }
                                            # Add Prestashop ID field if available
                                            if 'x_prestashop_category_id' in category_model._fields:
                                                category_vals['x_prestashop_category_id'] = int(category_id)
                                            
                                            category_obj = category_model.create(category_vals)
                                            imported_count += 1
                                            _logger.info("Created category: %s (Prestashop ID: %s)", category_obj.name, category_id)
                                        except Exception as create_error:
                                            _logger.error("Error creating category %s: %s", category_id, str(create_error))
                                            error_count += 1
                                    else:
                                        # Update existing category with Prestashop ID if missing
                                        if ('x_prestashop_category_id' in category_model._fields and 
                                            not existing_category.x_prestashop_category_id):
                                            try:
                                                existing_category.write({'x_prestashop_category_id': int(category_id)})
                                                _logger.info("Updated category %s with Prestashop ID %s", existing_category.name, category_id)
                                            except Exception as update_error:
                                                _logger.warning("Failed to update category %s with Prestashop ID: %s", existing_category.name, str(update_error))
                                        
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
        """Import products from Prestashop using background job to avoid timeout"""
        return self._start_background_import('products')

    def _background_product_import(self):
        """Background product import with enhanced transaction safety"""
        def safe_product_import():
            """Internal function with all import logic wrapped in transaction safety"""
            # Check for required fields and show recommendations only if missing
            product_model = self.env['product.template']
            category_model = self.env['product.category']
            
            if 'x_prestashop_product_id' not in product_model._fields:
                _logger.info("📢 Recommendation: For robust product import and to prevent duplicates, consider adding an integer field like 'x_prestashop_product_id' to the 'product.template' model in Odoo to store the Prestashop product ID.")
            
            if 'x_prestashop_category_id' not in category_model._fields:
                _logger.info("📢 Ensure 'product.category' has an 'x_prestashop_category_id' field for correct category linking, as categories should be imported first.")

            def _perform_product_import():
                """Inner function containing the main product import logic."""
                base_url = self.prestashop_url.rstrip('/')
                if not base_url.endswith('/api'):
                    base_url += '/api'

                session = requests.Session()
                session.headers.update({'User-Agent': 'Odoo-Prestashop-Importer/1.0'})

                context = dict(self.env.context)
                context.update({
                    'tracking_disable': True, 'mail_create_nolog': True, 'mail_create_nosubscribe': True,
                    'mail_auto_subscribe_no_notify': True, 'mail_auto_delete': False, 'mail_create_uid': False,
                    'no_reset_password': True, 'rating_disable': True, 'no_rating': True, 'rating_skip': True,
                    'compute_disable': True, 'auto_compute_disable': True, 'prefetch_disable': True,
                    'import_mode': True, 'mass_import': True, 'install_mode': True, 'active_test': False,
                    'no_validate': True, 
                    'allowed_company_ids': [self.company_id.id if self.company_id else self.env.company.id],
                    'defer_parent_store_computation': True,
                })

                product_model = self.env['product.template'].with_context(context)
                category_model = self.env['product.category'].with_context(context)
                company_id = self.company_id.id if self.company_id else self.env.company.id

                # Helper for multilingual fields
                def get_multilang_field(xml_element, field_name, default_lang_id='1'):
                    if xml_element is None: return ''
                    field_node = xml_element.find(field_name)
                    if field_node is not None:
                        for lang_node in field_node.findall('language'):
                            if lang_node.get('id') == default_lang_id and lang_node.text:
                                return lang_node.text.strip()
                        for lang_node in field_node.findall('language'): # Fallback
                            if lang_node.text:
                                return lang_node.text.strip()
                    return ''

                products_list_url = f"{base_url}/products?ws_key={self.api_key}&display=[id]"
                _logger.info("🚀 Starting product import: Fetching product list from %s", products_list_url)

                try:
                    _logger.info("🌐 About to make API request to: %s", products_list_url)
                    list_response = session.get(products_list_url, timeout=300) 
                    _logger.info("✅ Successfully got product list in %.2f seconds", list_response.elapsed.total_seconds())
                    _logger.info("📊 Response status code: %s", list_response.status_code)
                    _logger.info("📊 Response content length: %d bytes", len(list_response.content))
                    _logger.info("📊 Response content type: %s", list_response.headers.get('content-type', 'Unknown'))
                    _logger.info("📊 First 1000 chars of response: %s", list_response.text[:1000])
                    list_response.raise_for_status()
                except requests.exceptions.Timeout:
                    _logger.error("🚨 TIMEOUT exception occurred")
                    return self._create_error_report("❌ TIMEOUT ERROR - Product Import Failed", "Connection timeout while getting product list (>300 seconds).", context="TIMEOUT SOLUTIONS:\n• Your Prestashop server might be slow or the product list is very large.\n• Consider server resources or API filters if available.")
                except requests.exceptions.ConnectionError as ce:
                    _logger.error("🚨 CONNECTION ERROR exception occurred: %s", str(ce))
                    return self._create_error_report("❌ CONNECTION ERROR - Product Import Failed", "Cannot connect to Prestashop server to get product list.", context="CONNECTION SOLUTIONS:\n• Check your internet connection and Prestashop server status.")
                except requests.exceptions.HTTPError as he:
                    _logger.error("🚨 HTTP ERROR exception occurred: %s", str(he))
                    return self._create_error_report("❌ HTTP ERROR - Product Import Failed", f"Prestashop API returned HTTP {he.response.status_code} for product list.", context=f"HTTP ERROR DETAILS:\nStatus Code: {he.response.status_code}\nResponse: {he.response.text[:500]}...")
                except Exception as e_list:
                    _logger.error("🚨 UNEXPECTED ERROR exception occurred: %s", str(e_list))
                    return self._create_error_report("❌ UNEXPECTED ERROR - Product List Failed", f"An unexpected error occurred fetching product list: {str(e_list)}", context="Check Odoo logs for more details.")

                try:
                    root = ET.fromstring(list_response.content)
                    _logger.info("🔍 XML Root tag: %s", root.tag)
                    _logger.info("🔍 XML Root attributes: %s", root.attrib)
                    
                    # Try different ways to find product IDs
                    id_elements = root.findall('.//products/product/id')
                    _logger.info("🔍 Found %d elements with './/products/product/id' xpath", len(id_elements))
                    
                    # Try alternative xpath patterns
                    id_elements_alt1 = root.findall('.//product/id')
                    _logger.info("🔍 Found %d elements with './/product/id' xpath", len(id_elements_alt1))
                    
                    id_elements_alt2 = root.findall('.//id')
                    _logger.info("🔍 Found %d elements with './/id' xpath", len(id_elements_alt2))
                    
                    # Log the structure we're working with
                    _logger.info("🔍 XML structure preview:")
                    for child in root:
                        _logger.info("🔍   Child tag: %s, attrib: %s", child.tag, child.attrib)
                        for subchild in child:
                            _logger.info("🔍     Subchild tag: %s, attrib: %s, text: %s", subchild.tag, subchild.attrib, subchild.text[:50] if subchild.text else None)
                            break  # Only show first subchild
                        break  # Only show first child
                    
                    product_ids = [id_el.text for id_el in id_elements if id_el.text and id_el.text.strip()]
                    _logger.info("🔍 Final product_ids list: %s", product_ids[:10])  # Show first 10
                except ET.ParseError as e_parse_list:
                    error_context_xml_preview = "Could not decode XML content for preview."
                    problematic_xml_content = "Could not retrieve or decode XML content."
                    try:
                        problematic_xml_content = list_response.content.decode('utf-8', errors='replace')
                        error_context_xml_preview = problematic_xml_content[:500]
                    except Exception as decode_err:
                        _logger.error("Failed to decode XML for error report preview: %s", str(decode_err))
                    
                    _logger.error(
                        "Problematic XML content snippet (first 2KB) causing ParseError for product list: %s", 
                        problematic_xml_content[:2000]
                    )
                    
                    if len(problematic_xml_content) < 10000:
                        _logger.error("Full problematic XML content (length: %d):\n%s", len(problematic_xml_content), problematic_xml_content)
                    else:
                        _logger.error("Problematic XML content is too large to log fully (length: %d). Logging first 5KB and last 5KB.", len(problematic_xml_content))
                        _logger.error("First 5KB of problematic XML:\n%s", problematic_xml_content[:5000])
                        _logger.error("Last 5KB of problematic XML:\n%s", problematic_xml_content[-5000:])

                    return self._create_error_report(
                        "❌ XML PARSE ERROR - Product List Failed", 
                        f"Invalid XML response for product list: {str(e_parse_list)}", 
                        context=f"XML ERROR DETAILS:\\nResponse (first 500 chars): {error_context_xml_preview}"
                    )

                if not product_ids:
                    return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': 'ℹ️ No Products Found', 'message': 'No product IDs were found in the Prestashop API response.', 'type': 'info', 'sticky': False}}

                imported_count, skipped_count, error_count = 0, 0, 0
                total_products = len(product_ids)
                _logger.info("Found %d product IDs to process.", total_products)

                for i, ps_product_id in enumerate(product_ids):
                    self._log_import_progress(i + 1, total_products, imported_count, skipped_count, error_count, "product")

                    if error_count > 20 and (error_count / max(i + 1, 1)) > 0.25:
                        _logger.error("🚨 Too many errors (%d/%d), stopping product import.", error_count, i + 1)
                        return self._create_error_report("❌ IMPORT STOPPED - Too Many Product Errors!", f"Import stopped after {i+1} products due to high error rate ({error_count} errors).", imported_count, skipped_count, error_count, context="HIGH ERROR RATE SOLUTIONS:\n• Check Prestashop API stability.\n• Review individual product errors in Odoo logs.")

                    # Check if product already exists (using x_prestashop_product_id field if available)
                    if 'x_prestashop_product_id' in product_model._fields:
                        if product_model.search_count([('x_prestashop_product_id', '=', ps_product_id), ('company_id', '=', company_id)]):
                            _logger.info("Product with Prestashop ID %s already exists. Skipping.", ps_product_id)
                            skipped_count += 1
                            continue
                    else:
                        _logger.warning("Field 'x_prestashop_product_id' not found on 'product.template'. Duplicate checking will be less reliable (e.g., by SKU if implemented).")

                    product_detail_url = f"{base_url}/products/{ps_product_id}?ws_key={self.api_key}"
                    detail_response = None

                    try:
                        for attempt_detail in range(3):
                            try:
                                _logger.debug("Attempt %d: Fetching details for product %s", attempt_detail + 1, ps_product_id)
                                detail_response = session.get(product_detail_url, timeout=60)
                                detail_response.raise_for_status()
                                _logger.debug("Successfully fetched product %s details in %.2f seconds", ps_product_id, detail_response.elapsed.total_seconds())
                                break
                            except requests.exceptions.Timeout:
                                _logger.warning("Timeout fetching product %s (attempt %d/3)", ps_product_id, attempt_detail + 1)
                                if attempt_detail == 2: error_count += 1; _logger.error("Final timeout for product %s.", ps_product_id); break
                                time.sleep(2 * (attempt_detail + 1))
                            except requests.exceptions.ConnectionError as ce:
                                _logger.warning("Connection error for product %s (attempt %d/3): %s", ps_product_id, attempt_detail + 1, str(ce))
                                if attempt_detail == 2: error_count += 1; _logger.error("Final connection error for product %s.", ps_product_id); break
                                time.sleep(2 * (attempt_detail + 1))
                            except requests.exceptions.HTTPError as he_detail:
                                _logger.error("HTTP error for product %s: %s. Response: %s", ps_product_id, str(he_detail), he_detail.response.text[:200] if he_detail.response else "N/A")
                                error_count += 1; break
                            except Exception as e_detail_fetch:
                                _logger.error("Unexpected error fetching product %s (attempt %d/3): %s", ps_product_id, attempt_detail + 1, str(e_detail_fetch))
                                if attempt_detail == 2: error_count += 1; _logger.error("Final unexpected error fetching product %s.", ps_product_id); break
                                time.sleep(2 * (attempt_detail + 1))
                        
                        if detail_response is None or detail_response.status_code != 200:
                            _logger.error("Skipping product %s due to failed detail retrieval.", ps_product_id)
                            continue

                        try:
                            product_data_xml_root = ET.fromstring(detail_response.content)
                            product_data_xml = product_data_xml_root.find('product')
                            if product_data_xml is None:
                                _logger.error("Could not find <product> tag in XML for product ID %s. XML: %s", ps_product_id, detail_response.text[:500])
                                error_count += 1; continue

                            # --- Basic Product Data ---
                            name = get_multilang_field(product_data_xml, 'name')
                            if not name:
                                _logger.warning("Product ID %s has no name. Skipping.", ps_product_id)
                                error_count += 1; continue
                            
                            reference = product_data_xml.findtext('reference', default='')
                            ean13 = product_data_xml.findtext('ean13', default='')
                            
                            try:
                                price_str = product_data_xml.findtext('price', default='0.0')
                                list_price = float(price_str) if price_str else 0.0
                            except ValueError:
                                _logger.warning("Invalid price format for product %s: '%s'. Defaulting to 0.0.", ps_product_id, price_str)
                                list_price = 0.0

                            try:
                                cost_price_str = product_data_xml.findtext('wholesale_price', default='0.0')
                                standard_price = float(cost_price_str) if cost_price_str else 0.0
                            except ValueError:
                                _logger.warning("Invalid wholesale_price format for product %s: '%s'. Defaulting to 0.0.", ps_product_id, cost_price_str)
                                standard_price = 0.0
                            
                            description_sale = get_multilang_field(product_data_xml, 'description')
                            description_short = get_multilang_field(product_data_xml, 'description_short')
                            
                            active = product_data_xml.findtext('active') == '1'
                            
                            try:
                                weight_str = product_data_xml.findtext('weight', default='0.0')
                                weight = float(weight_str) if weight_str else 0.0
                            except ValueError:
                                _logger.warning("Invalid weight format for product %s: '%s'. Defaulting to 0.0.", ps_product_id, weight_str)
                                weight = 0.0

                            ps_product_type = product_data_xml.findtext('product_type', default='standard').lower()
                            odoo_product_type = 'consu'
                            if ps_product_type == 'virtual':
                                odoo_product_type = 'service'
                            elif ps_product_type == 'pack':
                                odoo_product_type = 'consu'
                                _logger.info("Prestashop 'pack' product %s imported as storable. BoM/kit functionality not implemented.", ps_product_id)
                            
                            # --- Category Mapping with Auto-Creation ---
                            ps_default_category_id = product_data_xml.findtext('id_category_default')
                            associations = product_data_xml.find('associations')
                            
                            # Use the improved category mapping method that auto-creates missing categories
                            odoo_category_ids = self._get_product_categories(base_url, ps_product_id, ps_default_category_id, associations)
                            
                            # --- Extract Image IDs for Import ---
                            image_ids = []
                            if associations is not None:
                                images_elem = associations.find('images')
                                if images_elem is not None:
                                    for img in images_elem.findall('image'):
                                        img_id = img.find('id')
                                        if img_id is not None and img_id.text:
                                            image_ids.append(img_id.text)
                                    _logger.debug("🖼️ Found %d image IDs for product %s: %s", len(image_ids), ps_product_id, image_ids)
                            
                            if not odoo_category_ids and ps_default_category_id:
                                 _logger.warning("Product %s: Default category ID %s (and potentially others) could not be mapped to Odoo categories. Product will be imported without internal category.", ps_product_id, ps_default_category_id)
                            elif not odoo_category_ids:
                                _logger.info("Product %s has no categories specified or mapped in Prestashop.", ps_product_id)

                            # --- Prepare Product Vals ---
                            product_vals = {
                                'name': name,
                                'default_code': reference,
                                'barcode': ean13 if ean13 and ean13 != '0' else False,
                                'list_price': list_price,
                                'standard_price': standard_price,
                                'description_sale': description_sale,
                                'description_picking': description_short or description_sale,
                                'active': active,
                                'weight': weight,
                                'type': odoo_product_type,
                                'company_id': company_id,
                                'is_published': True,  # Set as published on website by default
                            }
                            
                            # Add website-specific description if available
                            if 'website_description' in product_model._fields:
                                product_vals['website_description'] = description_sale
                            
                            if 'x_prestashop_product_id' in product_model._fields:
                                product_vals['x_prestashop_product_id'] = int(ps_product_id)
                            
                            # Assign internal category (categ_id) - use first category found
                            if odoo_category_ids:
                                product_vals['categ_id'] = odoo_category_ids[0]
                                if len(odoo_category_ids) > 1:
                                    _logger.info("Product %s: Multiple categories found (%d), using first category (ID %d) as internal category.", ps_product_id, len(odoo_category_ids), odoo_category_ids[0])
                            else:
                                # Use default category if no specific category is mapped
                                default_category = category_model.search([('name', '=', 'All')], limit=1)
                                if default_category:
                                    product_vals['categ_id'] = default_category.id
                                    _logger.info("Product %s: No specific category mapped, using default 'All' category (ID %d).", ps_product_id, default_category.id)

                            # --- Create Product ---
                            try:
                                new_product = product_model.create(product_vals)
                                _logger.info("🎉 Created new product '%s' (Odoo ID %d) from Prestashop ID %s.", new_product.name, new_product.id, ps_product_id)
                                
                                # Commit the product creation to avoid transaction rollback issues
                                self.env.cr.commit()
                                
                            except Exception as create_error:
                                _logger.error("❌ Failed to create product %s: %s", ps_product_id, str(create_error))
                                self.env.cr.rollback()
                                error_count += 1
                                continue
                            
                            # --- Import Product Images ---
                            if image_ids:
                                try:
                                    _logger.debug("🖼️ Starting image import for product %s with %d image IDs", ps_product_id, len(image_ids))
                                    self._import_product_images(new_product, ps_product_id, image_ids, session, base_url)
                                    # Check if image was actually set
                                    if new_product.image_1920:
                                        _logger.info("✅ Images imported for product %s (image data: %d bytes)", ps_product_id, len(new_product.image_1920))
                                    else:
                                        _logger.warning("⚠️ Image import completed but no image data found for product %s", ps_product_id)
                                except Exception as img_error:
                                    _logger.warning("❌ Failed to import images for product %s: %s", ps_product_id, str(img_error))
                            else:
                                _logger.debug("🖼️ No image IDs found for product %s", ps_product_id)
                                # Don't rollback for image errors, just continue
                            
                            # --- Import Product Categories ---
                            try:
                                # Get internal categories for categ_id
                                internal_category_ids = self._get_product_categories(base_url, ps_product_id, ps_default_category_id, associations)
                                if internal_category_ids:
                                    new_product.write({'categ_id': internal_category_ids[0]})
                                    _logger.info("✅ Internal category assigned to product %s: %s", ps_product_id, internal_category_ids[0])
                                
                                # Get public categories (website categories) - ONLY if model exists
                                _logger.debug("🏷️ Getting public categories for product %s", ps_product_id)
                                
                                # First check if public category model actually exists
                                public_category_model = None
                                possible_models = ['product.public.category', 'website.product.category']
                                
                                for model_name in possible_models:
                                    try:
                                        test_model = self.env[model_name]
                                        if test_model:
                                            public_category_model = test_model
                                            _logger.debug("🏷️ Found public category model: %s", model_name)
                                            break
                                    except KeyError:
                                        continue
                                
                                if public_category_model:
                                    # Only proceed if we have a valid public category model
                                    public_category_ids = self._get_product_public_categories(base_url, ps_product_id, ps_default_category_id, associations)
                                    _logger.debug("🏷️ Public categories found for product %s: %s", ps_product_id, public_category_ids)
                                    
                                    if public_category_ids:
                                        # Try different field names depending on Odoo version/setup
                                        public_field_name = None
                                        for field_name in ['public_categ_ids', 'website_categ_ids']:
                                            if field_name in new_product._fields:
                                                public_field_name = field_name
                                                _logger.debug("🏷️ Found public category field: %s", field_name)
                                                break
                                        
                                        if public_field_name:
                                            # Triple validation to ensure we only use valid public category IDs
                                            try:
                                                final_valid_ids = []
                                                for cat_id in public_category_ids:
                                                    # Verify the ID exists in the public category table
                                                    if public_category_model.browse(cat_id).exists():
                                                        final_valid_ids.append(cat_id)
                                                        _logger.debug("🏷️ Validated public category ID %s", cat_id)
                                                    else:
                                                        _logger.warning("🏷️ Public category ID %s does not exist in %s table, skipping", cat_id, public_category_model._name)
                                                
                                                if final_valid_ids:
                                                    new_product.write({public_field_name: [(6, 0, final_valid_ids)]})
                                                    _logger.info("✅ Public categories assigned to product %s via %s: %s", ps_product_id, public_field_name, final_valid_ids)
                                                else:
                                                    _logger.warning("🏷️ No valid public categories after validation for product %s", ps_product_id)
                                            except Exception as validation_error:
                                                _logger.warning("❌ Error during public category validation for product %s: %s", ps_product_id, str(validation_error))
                                                # Don't rollback for category assignment errors
                                        else:
                                            _logger.warning("🏷️ No suitable public category field found for product %s", ps_product_id)
                                    else:
                                        _logger.debug("🏷️ No public categories returned for product %s", ps_product_id)
                                else:
                                    _logger.debug("🏷️ No public category model available - skipping public category assignment for product %s", ps_product_id)
                                    
                            except Exception as cat_error:
                                _logger.warning("❌ Failed to assign categories for product %s: %s", ps_product_id, str(cat_error))
                                # Don't rollback for category errors, just continue
                            
                            # --- Import Product Stock ---
                            try:
                                self._import_product_stock(base_url, ps_product_id, new_product)
                                _logger.info("✅ Stock imported for product %s", ps_product_id)
                            except Exception as stock_error:
                                _logger.warning("❌ Failed to import stock for product %s: %s", ps_product_id, str(stock_error))
                                # Don't rollback for stock errors, just continue
                            
                            # Commit successful product import
                            try:
                                self.env.cr.commit()
                                imported_count += 1
                            except Exception as commit_error:
                                _logger.error("❌ Failed to commit product %s: %s", ps_product_id, str(commit_error))
                                self.env.cr.rollback()
                                error_count += 1

                        except ET.ParseError as e_parse_prod:
                            _logger.error("XML Parse Error for product %s details: %s. XML: %s", ps_product_id, str(e_parse_prod), detail_response.text[:500] if detail_response else "N/A")
                            error_count += 1
                        except Exception as e_process_data:
                            _logger.error("Error processing data for product %s: %s", ps_product_id, str(e_process_data))
                            error_count +=1
                    
                    except Exception as e_outer_product_loop:
                        _logger.error("💥 Unexpected outer error processing Prestashop product ID %s: %s", ps_product_id, str(e_outer_product_loop))
                        error_count += 1
                    
                    time.sleep(0.1)

                # --- Final Report ---
                title = "✅ Product Import Successful!"
                message_parts = [f"Product import process completed.", f"• Imported: {imported_count} new products", f"• Skipped: {skipped_count} (already exist or invalid data)", f"• Errors: {error_count}"]
                notif_type = 'success'

                if error_count > 0:
                    title = "⚠️ Product Import Completed with ERRORS!"
                    notif_type = 'warning'
                if imported_count == 0 and total_products > 0:
                    if error_count == 0 and skipped_count == total_products:
                        title = "ℹ️ Product Import: No New Products"
                        message_parts = [f"All {total_products} products from Prestashop already exist in Odoo or were skipped due to invalid/missing data."]
                        notif_type = 'info'
                    elif error_count > 0 :
                        title = "❌ Product Import Failed to Import New Records"
                        notif_type = 'danger'
                return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': title, 'message': "\\n".join(message_parts), 'type': notif_type, 'sticky': error_count > 0}}
        
            return self._safe_database_operation(_perform_product_import, "complete product import")
        
        # Execute import with database safety
        try:
            result = self._safe_database_operation(safe_product_import, "complete product import")
            _logger.info("🎉 BACKGROUND PRODUCT IMPORT COMPLETED SUCCESSFULLY")
            return result
        except Exception as e:
            _logger.error("❌ BACKGROUND PRODUCT IMPORT FAILED: %s", str(e))
            return None

    def _get_product_categories(self, test_url, product_id, default_category_id, associations_elem):
        """Get and map Prestashop categories to Odoo categories with auto-creation and hierarchy support"""
        category_ids = []
        category_model = self.env['product.category']
        
        # Collect all category IDs from product
        ps_category_ids = []
        
        # Add default category
        if default_category_id and default_category_id not in ['1', '2']:  # Skip root categories
            ps_category_ids.append(default_category_id)
        
        # Add additional categories from associations
        if associations_elem is not None:
            categories_elem = associations_elem.find('categories')
            if categories_elem is not None:
                for cat_elem in categories_elem.findall('category'):
                    cat_id_elem = cat_elem.find('id')
                    if cat_id_elem is not None and cat_id_elem.text:
                        cat_id = cat_id_elem.text
                        if cat_id not in ['1', '2'] and cat_id not in ps_category_ids:
                            ps_category_ids.append(cat_id)
        
        # Map Prestashop categories to Odoo categories with auto-creation
        for ps_cat_id in ps_category_ids:
            try:
                ps_cat_id_int = int(ps_cat_id)
                
                # First, try to find by Prestashop ID (most reliable)
                if 'x_prestashop_category_id' in category_model._fields:
                    existing_category = category_model.search([
                        ('x_prestashop_category_id', '=', ps_cat_id_int)
                    ], limit=1)
                    
                    if existing_category:
                        category_ids.append(existing_category.id)
                        _logger.debug("Found existing category by Prestashop ID %s: %s", ps_cat_id, existing_category.name)
                        continue
                
                # If not found by ID, get category details from Prestashop and create it
                cat_url = f"{test_url}/categories/{ps_cat_id}?ws_key={self.api_key}"
                cat_response = requests.get(cat_url, timeout=30)
                
                if cat_response.status_code == 200:
                    cat_root = ET.fromstring(cat_response.content)
                    cat_element = cat_root.find('.//category')
                    
                    if cat_element is not None:
                        # Extract category data
                        name_elem = cat_element.find('.//name/language')
                        if name_elem is None:
                            name_elem = cat_element.find('name')
                        
                        cat_name = name_elem.text if name_elem is not None else f'Category {ps_cat_id}'
                        
                        # Extract parent ID for hierarchy support
                        parent_id_elem = cat_element.find('id_parent')
                        parent_ps_id = parent_id_elem.text if parent_id_elem is not None else None
                        
                        if cat_name and cat_name.strip():
                            # Create category with hierarchy support
                            odoo_category = self._create_category_with_hierarchy(
                                category_model, test_url, ps_cat_id_int, cat_name.strip(), parent_ps_id
                            )
                            
                            if odoo_category:
                                category_ids.append(odoo_category.id)
                                _logger.info("✅ Created/found category: %s (Prestashop ID: %s)", odoo_category.name, ps_cat_id)
                else:
                    _logger.warning("Failed to fetch category %s from Prestashop: HTTP %d", ps_cat_id, cat_response.status_code)
                
            except Exception as e:
                _logger.warning("Error processing category %s for product %s: %s", ps_cat_id, product_id, str(e))
        
        return category_ids
    
    def _create_category_with_hierarchy(self, category_model, test_url, ps_cat_id, cat_name, parent_ps_id):
        """Create category with parent hierarchy support"""
        try:
            # Skip root categories (1 and 2)
            if parent_ps_id and parent_ps_id not in ['1', '2', '0']:
                parent_ps_id_int = int(parent_ps_id)
                
                # Find or create parent category first
                parent_category = None
                if 'x_prestashop_category_id' in category_model._fields:
                    parent_category = category_model.search([
                        ('x_prestashop_category_id', '=', parent_ps_id_int)
                    ], limit=1)
                
                # If parent doesn't exist, create it recursively
                if not parent_category:
                    _logger.info("Creating parent category for Prestashop ID %s", parent_ps_id)
                    parent_category = self._fetch_and_create_category(category_model, test_url, parent_ps_id_int)
                
                # Create category with parent
                category_vals = {'name': cat_name}
                if 'x_prestashop_category_id' in category_model._fields:
                    category_vals['x_prestashop_category_id'] = ps_cat_id
                if parent_category:
                    category_vals['parent_id'] = parent_category.id
                    
                new_category = category_model.create(category_vals)
                _logger.info("Created category with parent: %s (under %s)", cat_name, parent_category.name if parent_category else 'root')
                return new_category
            else:
                # Create root-level category
                category_vals = {'name': cat_name}
                if 'x_prestashop_category_id' in category_model._fields:
                    category_vals['x_prestashop_category_id'] = ps_cat_id
                    
                new_category = category_model.create(category_vals)
                _logger.info("Created root category: %s", cat_name)
                return new_category
                
        except Exception as e:
            _logger.warning("Failed to create category %s with hierarchy: %s", cat_name, str(e))
            # Fallback: create simple category without hierarchy
            try:
                category_vals = {'name': cat_name}
                if 'x_prestashop_category_id' in category_model._fields:
                    category_vals['x_prestashop_category_id'] = ps_cat_id
                return category_model.create(category_vals)
            except Exception as fallback_error:
                _logger.error("Failed to create fallback category %s: %s", cat_name, str(fallback_error))
                return None
    
    def _fetch_and_create_category(self, category_model, test_url, ps_cat_id):
        """Fetch category data from Prestashop and create it recursively"""
        try:
            cat_url = f"{test_url}/categories/{ps_cat_id}?ws_key={self.api_key}"
            cat_response = requests.get(cat_url, timeout=30)
            
            if cat_response.status_code == 200:
                cat_root = ET.fromstring(cat_response.content)
                cat_element = cat_root.find('.//category')
                
                if cat_element is not None:
                    # Extract category data
                    name_elem = cat_element.find('.//name/language')
                    if name_elem is None:
                        name_elem = cat_element.find('name')
                    
                    cat_name = name_elem.text if name_elem is not None else f'Category {ps_cat_id}'
                    
                    # Extract parent ID
                    parent_id_elem = cat_element.find('id_parent')
                    parent_ps_id = parent_id_elem.text if parent_id_elem is not None else None
                    
                    # Create category with hierarchy
                    return self._create_category_with_hierarchy(
                        category_model, test_url, ps_cat_id, cat_name.strip(), parent_ps_id
                    )
            else:
                _logger.warning("Failed to fetch parent category %s: HTTP %d", ps_cat_id, cat_response.status_code)
                return None
                
        except Exception as e:
            _logger.error("Error fetching category %s: %s", ps_cat_id, str(e))
            return None
    
    def _get_product_public_categories(self, test_url, product_id, default_category_id, associations_elem):
        """Get and map Prestashop categories to Odoo public categories (website categories) with auto-creation"""
        public_category_ids = []
        
        # Check if public category model exists (different models in different Odoo versions)
        public_category_model = None
        possible_models = ['product.public.category', 'website.product.category', 'product_public_category']
        
        for model_name in possible_models:
            try:
                # Try to access the model
                test_model = self.env[model_name]
                if test_model:
                    public_category_model = test_model
                    _logger.debug("🏷️ Using public category model: %s", model_name)
                    break
            except KeyError:
                # Model doesn't exist in this Odoo version
                _logger.debug("🏷️ Model %s not available", model_name)
                continue
            except Exception as e:
                _logger.debug("🏷️ Error accessing model %s: %s", model_name, str(e))
                continue
        
        if not public_category_model:
            _logger.warning("🏷️ No public category model found (tried: %s). Skipping public category assignment to avoid errors.", possible_models)
            # Return empty list if no public category model exists to avoid foreign key constraint errors
            return []
        
        # Collect all category IDs from product
        ps_category_ids = []
        
        # Add default category
        if default_category_id and default_category_id not in ['1', '2']:  # Skip root categories
            ps_category_ids.append(default_category_id)
        
        # Add additional categories from associations
        if associations_elem is not None:
            categories_elem = associations_elem.find('categories')
            if categories_elem is not None:
                for cat_elem in categories_elem.findall('category'):
                    cat_id_elem = cat_elem.find('id')
                    if cat_id_elem is not None and cat_id_elem.text:
                        cat_id = cat_id_elem.text
                        if cat_id not in ['1', '2'] and cat_id not in ps_category_ids:
                            ps_category_ids.append(cat_id)
        
        if not ps_category_ids:
            _logger.debug("No valid categories found for product %s", product_id)
            return []
        
        # Map Prestashop categories to Odoo public categories with auto-creation
        for ps_cat_id in ps_category_ids:
            try:
                ps_cat_id_int = int(ps_cat_id)
                
                # First, try to find by Prestashop ID (most reliable)
                if 'x_prestashop_category_id' in public_category_model._fields:
                    existing_public_category = public_category_model.search([
                        ('x_prestashop_category_id', '=', ps_cat_id_int)
                    ], limit=1)
                    
                    if existing_public_category:
                        public_category_ids.append(existing_public_category.id)
                        _logger.debug("Found existing public category by Prestashop ID %s: %s", ps_cat_id, existing_public_category.name)
                        continue
                
                # If not found by ID, get category details from Prestashop and create it
                cat_url = f"{test_url}/categories/{ps_cat_id}?ws_key={self.api_key}"
                cat_response = requests.get(cat_url, timeout=30)
                
                if cat_response.status_code == 200:
                    cat_root = ET.fromstring(cat_response.content)
                    cat_element = cat_root.find('.//category')
                    
                    if cat_element is not None:
                        # Extract category name (try multiple approaches for multilingual)
                        cat_name = None
                        
                        # Try multilingual name first
                        name_elem = cat_element.find('.//name/language[@id="1"]')
                        if name_elem is None:
                            name_elem = cat_element.find('.//name/language')
                        if name_elem is None:
                            name_elem = cat_element.find('name')
                        
                        if name_elem is not None and name_elem.text:
                            cat_name = name_elem.text.strip()
                        
                        if not cat_name:
                            cat_name = f'Public Category {ps_cat_id}'
                        
                        # Extract parent ID for hierarchy support
                        parent_id_elem = cat_element.find('id_parent')
                        parent_ps_id = parent_id_elem.text if parent_id_elem is not None else None
                        
                        # Create public category with hierarchy support
                        odoo_public_category = self._create_public_category_with_hierarchy(
                            public_category_model, test_url, ps_cat_id_int, cat_name, parent_ps_id
                        )
                        
                        if odoo_public_category:
                            public_category_ids.append(odoo_public_category.id)
                            _logger.info("✅ Created/found public category: %s (Prestashop ID: %s)", odoo_public_category.name, ps_cat_id)
                else:
                    _logger.debug("Failed to fetch public category %s: HTTP %d", ps_cat_id, cat_response.status_code)
                
            except Exception as e:
                _logger.warning("Error processing public category %s for product %s: %s", ps_cat_id, product_id, str(e))
        
        return public_category_ids
    
    def _create_public_category_with_hierarchy(self, public_category_model, test_url, ps_cat_id, cat_name, parent_ps_id):
        """Create public category with parent hierarchy support"""
        try:
            # Skip root categories (1 and 2)
            if parent_ps_id and parent_ps_id not in ['1', '2', '0']:
                parent_ps_id_int = int(parent_ps_id)
                
                # Find or create parent category first
                parent_category = None
                if 'x_prestashop_category_id' in public_category_model._fields:
                    parent_category = public_category_model.search([
                        ('x_prestashop_category_id', '=', parent_ps_id_int)
                    ], limit=1)
                
                # If parent doesn't exist, create it recursively
                if not parent_category:
                    _logger.info("Creating parent public category for Prestashop ID %s", parent_ps_id)
                    parent_category = self._fetch_and_create_public_category(public_category_model, test_url, parent_ps_id_int)
                
                # Create public category with parent
                category_data = {'name': cat_name}
                if 'x_prestashop_category_id' in public_category_model._fields:
                    category_data['x_prestashop_category_id'] = ps_cat_id
                if parent_category:
                    category_data['parent_id'] = parent_category.id
                
                # Add website-specific fields if they exist
                if 'website_id' in public_category_model._fields:
                    website = self.env['website'].search([], limit=1)
                    if website:
                        category_data['website_id'] = website.id
                        
                new_public_category = public_category_model.create(category_data)
                _logger.info("Created public category with parent: %s (under %s)", cat_name, parent_category.name if parent_category else 'root')
                return new_public_category
            else:
                # Create root-level public category
                category_data = {'name': cat_name}
                if 'x_prestashop_category_id' in public_category_model._fields:
                    category_data['x_prestashop_category_id'] = ps_cat_id
                
                # Add website-specific fields if they exist
                if 'website_id' in public_category_model._fields:
                    website = self.env['website'].search([], limit=1)
                    if website:
                        category_data['website_id'] = website.id
                    
                new_public_category = public_category_model.create(category_data)
                _logger.info("Created root public category: %s", cat_name)
                return new_public_category
                
        except Exception as e:
            _logger.warning("Failed to create public category %s with hierarchy: %s", cat_name, str(e))
            # Fallback: create simple public category without hierarchy
            try:
                category_data = {'name': cat_name}
                if 'x_prestashop_category_id' in public_category_model._fields:
                    category_data['x_prestashop_category_id'] = ps_cat_id
                return public_category_model.create(category_data)
            except Exception as fallback_error:
                _logger.error("Failed to create fallback public category %s: %s", cat_name, str(fallback_error))
                return None
    
    def _fetch_and_create_public_category(self, public_category_model, test_url, ps_cat_id):
        """Fetch public category data from Prestashop and create it recursively"""
        try:
            cat_url = f"{test_url}/categories/{ps_cat_id}?ws_key={self.api_key}"
            cat_response = requests.get(cat_url, timeout=30)
            
            if cat_response.status_code == 200:
                cat_root = ET.fromstring(cat_response.content)
                cat_element = cat_root.find('.//category')
                
                if cat_element is not None:
                    # Extract category data
                    name_elem = cat_element.find('.//name/language')
                    if name_elem is None:
                        name_elem = cat_element.find('name')
                    
                    cat_name = name_elem.text if name_elem is not None else f'Public Category {ps_cat_id}'
                    
                    # Extract parent ID
                    parent_id_elem = cat_element.find('id_parent')
                    parent_ps_id = parent_id_elem.text if parent_id_elem is not None else None
                    
                    # Create public category with hierarchy
                    return self._create_public_category_with_hierarchy(
                        public_category_model, test_url, ps_cat_id, cat_name.strip(), parent_ps_id
                    )
            else:
                _logger.warning("Failed to fetch parent public category %s: HTTP %d", ps_cat_id, cat_response.status_code)
                return None
                
        except Exception as e:
            _logger.error("Error fetching public category %s: %s", ps_cat_id, str(e))
            return None

    def _get_product_taxes(self, test_url, tax_group_id):
        """Get and map Prestashop tax rules to Odoo taxes"""
        if not tax_group_id or tax_group_id == '0':
            return []
        
        tax_model = self.env['account.tax']
        tax_ids = []
        
        try:
            # Get tax rules group from Prestashop
            tax_url = f"{test_url}/tax_rule_groups/{tax_group_id}?ws_key={self.api_key}"
            tax_response = requests.get(tax_url, timeout=30)
            
            if tax_response.status_code == 200:
                tax_root = ET.fromstring(tax_response.content)
                tax_group_element = tax_root.find('.//tax_rule_group')
                
                if tax_group_element is not None:
                    # Get tax rules for this group
                    tax_rules_url = f"{test_url}/tax_rules?filter[id_tax_rule_group]={tax_group_id}&ws_key={self.api_key}"
                    tax_rules_response = requests.get(tax_rules_url, timeout=30)
                    
                    if tax_rules_response.status_code == 200:
                        rules_root = ET.fromstring(tax_rules_response.content)
                        tax_rules = rules_root.findall('.//tax_rule')
                        
                        for rule in tax_rules:
                            tax_id_elem = rule.find('id_tax')
                            if tax_id_elem is not None and tax_id_elem.text:
                                # Get individual tax details
                                tax_detail_url = f"{test_url}/taxes/{tax_id_elem.text}?ws_key={self.api_key}"
                                tax_detail_response = requests.get(tax_detail_url, timeout=30)
                                
                                if tax_detail_response.status_code == 200:
                                    tax_detail_root = ET.fromstring(tax_detail_response.content)
                                    tax_element = tax_detail_root.find('.//tax')
                                    
                                    if tax_element is not None:
                                        rate_elem = tax_element.find('rate')
                                        name_elem = tax_element.find('.//name/language')
                                        if name_elem is None:
                                            name_elem = tax_element.find('name')
                                        
                                        if rate_elem is not None and name_elem is not None:
                                            tax_rate = float(rate_elem.text) if rate_elem.text else 0.0
                                            tax_name = name_elem.text if name_elem.text else f'Tax {tax_rate}%'
                                            
                                            # Find or create tax in Odoo
                                            existing_tax = tax_model.search([
                                                ('amount', '=', tax_rate),
                                                ('type_tax_use', '=', 'sale'),
                                                ('amount_type', '=', 'percent')
                                            ], limit=1)
                                            
                                            if existing_tax:
                                                tax_ids.append(existing_tax.id)
                                                _logger.debug("Found existing tax: %s (%s%%)", tax_name, tax_rate)
                                            else:
                                                # Create new tax
                                                try:
                                                    new_tax = tax_model.create({
                                                        'name': f'{tax_name} ({tax_rate}%)',
                                                        'amount': tax_rate,
                                                        'amount_type': 'percent',
                                                        'type_tax_use': 'sale',
                                                        'description': f'Imported from Prestashop (Group: {tax_group_id})',
                                                    })
                                                    tax_ids.append(new_tax.id)
                                                    _logger.info("Created new tax: %s (%s%%)", tax_name, tax_rate)
                                                except Exception as e:
                                                    _logger.warning("Failed to create tax %s: %s", tax_name, str(e))
        
        except Exception as e:
            _logger.warning("Error processing tax group %s: %s", tax_group_id, str(e))
        
        return tax_ids
    
    def _import_product_images(self, product_obj, product_id, image_ids, session, base_url):
        """Import product images from Prestashop"""
        if not image_ids:
            return
        
        _logger.info("🖼️ Processing %d images for product %s", len(image_ids), product_obj.name)
        
        imported_count = 0
        error_count = 0
        
        def _build_direct_image_url(img_id, base_url):
            """Build direct image URL for PrestaShop 1.6 using filesystem pattern"""
            # PrestaShop 1.6 stores images in /img/p/ with digit separation pattern
            # For image ID 8860, path becomes: /img/p/8/8/6/0/8860.jpg
            img_str = str(img_id)
            path_parts = list(img_str)
            path = '/'.join(path_parts)
            return f"{base_url.replace('/api', '')}/img/p/{path}/{img_id}.jpg"
        
        # Process each image
        for i, img_id in enumerate(image_ids):
            success = False
            try:
                # Try Method 1: Direct filesystem URL (usually works better)
                direct_image_url = _build_direct_image_url(img_id, base_url)
                _logger.debug("📥 Trying direct image URL: %s", direct_image_url)
                
                response = session.get(direct_image_url, timeout=30)
                
                if response.status_code == 200:
                    success = True
                    _logger.debug("✅ Direct URL worked for image %s", img_id)
                else:
                    # Try Method 2: API webservice URL as fallback
                    api_image_url = f"{base_url}/images/products/{product_id}/{img_id}?ws_key={self.api_key}"
                    _logger.debug("📥 Trying API URL as fallback: %s", api_image_url)
                    
                    response = session.get(api_image_url, timeout=30)
                    if response.status_code == 200:
                        success = True
                        _logger.debug("✅ API URL worked for image %s", img_id)
                
                if success:
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
                                try:
                                    # Check if product.image model exists
                                    product_image_model = self.env['product.image']
                                    product_image_model.create({
                                        'name': f'Prestashop Image {img_id}',
                                        'image_1920': image_data,
                                        'product_tmpl_id': product_obj.id,
                                    })
                                    _logger.info("✅ Added additional image for product %s (Prestashop Image ID: %s)", 
                                               product_obj.name, img_id)
                                    imported_count += 1
                                except Exception as model_error:
                                    # Fallback: just set as main image if product.image not available
                                    if not product_obj.image_1920:
                                        product_obj.write({'image_1920': image_data})
                                        imported_count += 1
                                        _logger.info("✅ Set as main image (fallback) for product %s", product_obj.name)
                                        
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
                    _logger.warning("❌ Failed to download image %s with both methods (direct + API)", img_id)
                    
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
        if imported_count == 0 and len(image_ids) > 0:
            _logger.warning("⚠️ Image import completed but no image data found for product %s", product_id)

    def _import_product_stock(self, test_url, product_id, product_obj):
        """Import product stock/inventory from Prestashop"""
        try:
            # Get stock available from Prestashop
            stock_url = f"{test_url}/stock_availables?ws_key={self.api_key}&filter[id_product]={product_id}"
            _logger.debug("Fetching stock for product %s from: %s", product_id, stock_url)
            stock_response = requests.get(stock_url, timeout=30)
            
            if stock_response.status_code == 200:
                stock_root = ET.fromstring(stock_response.content)
                stock_elements = stock_root.findall('.//stock_available')
                
                if not stock_elements:
                    _logger.debug("No stock information found for product %s", product_id)
                    return
                
                for stock_elem in stock_elements:
                    try:
                        quantity_str = stock_elem.findtext('quantity', '0')
                        quantity = float(quantity_str) if quantity_str else 0.0
                        
                        _logger.debug("Found stock quantity %s for product %s", quantity, product_id)
                        
                        # Update product stock if quantity > 0
                        if quantity > 0:
                            # Get the product variant (assuming first variant)
                            product_variant = product_obj.product_variant_ids and product_obj.product_variant_ids[0]
                            if product_variant:
                                # Get default internal location
                                location = self.env['stock.location'].search([
                                    ('usage', '=', 'internal'),
                                    ('company_id', '=', product_obj.company_id.id)
                                ], limit=1)
                                
                                if not location:
                                    location = self.env['stock.location'].search([('usage', '=', 'internal')], limit=1)
                                
                                if location:
                                    # Use sudo to ensure we have access to stock operations
                                    stock_quant = self.env['stock.quant'].sudo()
                                    
                                    # Check if quant already exists
                                    existing_quant = stock_quant.search([
                                        ('product_id', '=', product_variant.id),
                                        ('location_id', '=', location.id)
                                    ])
                                    
                                    if existing_quant:
                                        # Update existing quant
                                        existing_quant.write({'quantity': quantity})
                                        _logger.info("✅ Updated stock quantity %s for product %s", quantity, product_obj.name)
                                    else:
                                        # Create new quant
                                        stock_quant.create({
                                            'product_id': product_variant.id,
                                            'location_id': location.id,
                                            'quantity': quantity,
                                        })
                                        _logger.info("✅ Set initial stock quantity %s for product %s", quantity, product_obj.name)
                                        
                                    # Also update the product template's qty_available if needed
                                    try:
                                        product_obj.invalidate_cache(['qty_available', 'virtual_available'])
                                        _logger.debug("Stock cache invalidated for product %s", product_obj.name)
                                    except Exception as cache_error:
                                        _logger.debug("Cache invalidation minor issue for product %s: %s", product_obj.name, str(cache_error))
                                        
                                else:
                                    _logger.warning("No internal location found for stock update")
                            else:
                                _logger.warning("No product variant found for product %s", product_obj.name)
                        else:
                            _logger.debug("Product %s has zero or negative stock (%s), skipping stock update", product_id, quantity)
                        
                        break  # Use first stock_available record
                        
                    except Exception as e:
                        _logger.warning("Failed to process stock for product %s: %s", product_id, str(e))
                        
            elif stock_response.status_code == 404:
                _logger.debug("No stock endpoint found for product %s", product_id)
            else:
                _logger.warning("Failed to fetch stock for product %s: HTTP %d", product_id, stock_response.status_code)
                
        except Exception as e:
            _logger.warning("Error importing stock for product %s: %s", product_id, str(e))

    def _import_customer_addresses(self, test_url, customer_id, partner):
        """
        Import addresses for a specific customer from PrestaShop
        Returns: (imported_count, skipped_count, error_count)
        """
        imported_count = 0
        skipped_count = 0
        error_count = 0
        
        try:
            # Get customer addresses from PrestaShop API
            addresses_url = f"{test_url}/addresses?filter[id_customer]={customer_id}&ws_key={self.api_key}"
            _logger.debug("Fetching addresses for customer %s from: %s", customer_id, addresses_url)
            
            try:
                addresses_response = requests.get(addresses_url, timeout=30)
                addresses_response.raise_for_status()
            except requests.exceptions.Timeout:
                _logger.warning("Timeout fetching addresses for customer %s", customer_id)
                error_count += 1
                return imported_count, skipped_count, error_count
            except requests.exceptions.RequestException as e:
                _logger.warning("Error fetching addresses for customer %s: %s", customer_id, str(e))
                error_count += 1
                return imported_count, skipped_count, error_count
            
            if addresses_response.status_code != 200:
                _logger.warning("Failed to get addresses for customer %s: HTTP %s", customer_id, addresses_response.status_code)
                error_count += 1
                return imported_count, skipped_count, error_count
            
            # Parse addresses XML
            try:
                addresses_root = ET.fromstring(addresses_response.content)
                addresses = addresses_root.findall('.//address')
            except ET.ParseError as e:
                _logger.warning("Invalid XML response for addresses of customer %s: %s", customer_id, str(e))
                error_count += 1
                return imported_count, skipped_count, error_count
            
            if not addresses:
                _logger.debug("No addresses found for customer %s", customer_id)
                return imported_count, skipped_count, error_count
            
            # Process each address
            for address in addresses:
                address_id = address.get('id')
                if not address_id:
                    skipped_count += 1
                    continue
                
                try:
                    # Get detailed address data
                    address_detail_url = f"{test_url}/addresses/{address_id}?ws_key={self.api_key}"
                    
                    try:
                        address_detail_response = requests.get(address_detail_url, timeout=30)
                        address_detail_response.raise_for_status()
                    except requests.exceptions.RequestException as e:
                        _logger.warning("Error fetching address %s details: %s", address_id, str(e))
                        error_count += 1
                        continue
                    
                    if address_detail_response.status_code != 200:
                        _logger.warning("Failed to get address %s details: HTTP %s", address_id, address_detail_response.status_code)
                        error_count += 1
                        continue
                    
                    # Parse address details
                    try:
                        address_detail_root = ET.fromstring(address_detail_response.content)
                        address_element = address_detail_root.find('.//address')
                        
                        if address_element is not None:
                            # Extract address data
                            alias = address_element.findtext('alias', 'Address')
                            firstname = address_element.findtext('firstname', '')
                            lastname = address_element.findtext('lastname', '')
                            company = address_element.findtext('company', '')
                            address1 = address_element.findtext('address1', '')
                            address2 = address_element.findtext('address2', '')
                            postcode = address_element.findtext('postcode', '')
                            city = address_element.findtext('city', '')
                            phone = address_element.findtext('phone', '')
                            phone_mobile = address_element.findtext('phone_mobile', '')
                            
                            # Get country from id_country
                            country_id = address_element.findtext('id_country')
                            country = None
                            if country_id and country_id != '0':
                                # Try to find country by ISO code or create mapping
                                country = self._get_country_from_prestashop_id(country_id)
                            
                            # Get state from id_state  
                            state_id = address_element.findtext('id_state')
                            state = None
                            if state_id and state_id != '0' and country:
                                state = self._get_state_from_prestashop_id(state_id, country)
                            
                            # Build address name
                            address_name = f"{firstname} {lastname}".strip()
                            if not address_name:
                                address_name = alias or 'Address'
                            if company:
                                address_name = f"{company} - {address_name}"
                            
                            # Check if address already exists (by street and partner)
                            existing_address = self.env['res.partner'].search([
                                ('parent_id', '=', partner.id),
                                ('street', '=', address1),
                                ('zip', '=', postcode),
                                ('city', '=', city),
                            ], limit=1)
                            
                            if not existing_address:
                                # Determine address type based on alias
                                address_type = 'contact'  # Default
                                if 'invoice' in alias.lower() or 'billing' in alias.lower():
                                    address_type = 'invoice'
                                elif 'delivery' in alias.lower() or 'shipping' in alias.lower():
                                    address_type = 'delivery'
                                
                                # Create address as child partner
                                address_vals = {
                                    'name': address_name,
                                    'parent_id': partner.id,
                                    'type': address_type,
                                    'street': address1,
                                    'street2': address2 if address2 else False,
                                    'zip': postcode,
                                    'city': city,
                                    'phone': phone if phone else False,
                                    'mobile': phone_mobile if phone_mobile else False,
                                    'is_company': bool(company),
                                    'comment': f"Imported from Prestashop Address (ID: {address_id})",
                                }
                                
                                if country:
                                    address_vals['country_id'] = country.id
                                if state:
                                    address_vals['state_id'] = state.id
                                
                                address_partner = self.env['res.partner'].create(address_vals)
                                imported_count += 1
                                _logger.info("Created address: %s for customer %s (Prestashop Address ID: %s)", 
                                           address_partner.name, partner.name, address_id)
                            else:
                                skipped_count += 1
                                _logger.debug("Address already exists for customer %s", partner.name)
                        else:
                            error_count += 1
                            _logger.warning("No address data found for address ID %s", address_id)
                    
                    except ET.ParseError as e:
                        error_count += 1
                        _logger.warning("Invalid XML for address %s: %s", address_id, str(e))
                
                except Exception as e:
                    error_count += 1
                    _logger.error("Error processing address %s for customer %s: %s", address_id, customer_id, str(e))
                
                # Small delay between address requests
                time.sleep(0.1)
        
        except Exception as e:
            _logger.error("Error importing addresses for customer %s: %s", customer_id, str(e))
            error_count += 1
        
        return imported_count, skipped_count, error_count

    def _get_country_from_prestashop_id(self, prestashop_country_id):
        """
        Get Odoo country from PrestaShop country ID
        This is a simplified mapping - you may need to enhance this based on your data
        """
        try:
            # Get country details from PrestaShop API
            country_url = f"{self.prestashop_url.rstrip('/')}/api/countries/{prestashop_country_id}?ws_key={self.api_key}"
            response = requests.get(country_url, timeout=15)
            
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                country_element = root.find('.//country')
                if country_element is not None:
                    # Try to get ISO code
                    iso_code = country_element.findtext('iso_code')
                    if iso_code:
                        # Find country by ISO code
                        country = self.env['res.country'].search([('code', '=', iso_code.upper())], limit=1)
                        if country:
                            return country
                    
                    # Fallback: try by name
                    name_element = country_element.find('name/language')
                    if name_element is not None and name_element.text:
                        country_name = name_element.text
                        country = self.env['res.country'].search([('name', 'ilike', country_name)], limit=1)
                        if country:
                            return country
        except Exception as e:
            _logger.warning("Error fetching country %s from PrestaShop: %s", prestashop_country_id, str(e))
        
        return None

    def _get_state_from_prestashop_id(self, prestashop_state_id, country):
        """
        Get Odoo state from PrestaShop state ID
        """
        try:
            # Get state details from PrestaShop API
            state_url = f"{self.prestashop_url.rstrip('/')}/api/states/{prestashop_state_id}?ws_key={self.api_key}"
            response = requests.get(state_url, timeout=15)
            
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                state_element = root.find('.//state')
                if state_element is not None:
                    # Try to get state name
                    name = state_element.findtext('name')
                    iso_code = state_element.findtext('iso_code')
                    
                    if name:
                        # Search for state in the specified country
                        domain = [
                            ('country_id', '=', country.id),
                            '|',
                            ('name', 'ilike', name),
                            ('code', '=', iso_code) if iso_code else ('id', '=', 0)
                        ]
                        state = self.env['res.country.state'].search(domain, limit=1)
                        if state:
                            return state
        except Exception as e:
            _logger.warning("Error fetching state %s from PrestaShop: %s", prestashop_state_id, str(e))
        
        return None

    def _create_public_category_from_internal(self, internal_category):
        """Create a public category that mirrors an internal category"""
        try:
            public_category_model = self.env['product.public.category']
            
            # Check if a public category with the same Prestashop ID already exists
            if hasattr(internal_category, 'x_prestashop_category_id') and internal_category.x_prestashop_category_id:
                existing_public = public_category_model.search([
                    ('x_prestashop_category_id', '=', internal_category.x_prestashop_category_id)
                ], limit=1)
                if existing_public:
                    return existing_public

            # Create new public category
            category_data = {
                'name': internal_category.name,
            }
            
            # Add Prestashop ID if available
            if hasattr(internal_category, 'x_prestashop_category_id') and internal_category.x_prestashop_category_id:
                if 'x_prestashop_category_id' in public_category_model._fields:
                    category_data['x_prestashop_category_id'] = internal_category.x_prestashop_category_id
            
            # Handle parent relationship
            if internal_category.parent_id:
                # Try to find or create parent public category
                parent_public = self._create_public_category_from_internal(internal_category.parent_id)
                if parent_public:
                    category_data['parent_id'] = parent_public.id
            
            # Add website-specific fields if they exist
            if 'website_id' in public_category_model._fields:
                website = self.env['website'].search([], limit=1)
                if website:
                    category_data['website_id'] = website.id
            
            new_public_category = public_category_model.create(category_data)
            _logger.info("✅ Created public category from internal: %s (ID: %s)", new_public_category.name, new_public_category.id)
            return new_public_category
            
        except Exception as e:
            _logger.warning("❌ Failed to create public category from internal category %s: %s", internal_category.name, str(e))
            return None
