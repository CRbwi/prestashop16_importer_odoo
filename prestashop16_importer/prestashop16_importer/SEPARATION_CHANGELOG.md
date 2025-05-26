# Prestashop 1.6 Importer - Function Separation Changelog

## Changes Made

### 1. Backend Model Changes (`models/prestashop_backend.py`)

**BEFORE:** Single combined function
- `action_import_products_categories()` - Imported both products and categories together

**AFTER:** Separated into two independent functions
- `action_import_categories()` - Imports only categories from Prestashop
- `action_import_products()` - Imports only products from Prestashop

### 2. View Changes (`views/prestashop_backend_views.xml`)

**BEFORE:** Single button
- "Import Products & Categories" button

**AFTER:** Two separate buttons
- "Import Categories" button - triggers `action_import_categories`
- "Import Products" button - triggers `action_import_products`

## Function Details

### `action_import_categories()`
- **Purpose:** Import categories from Prestashop with proper error handling
- **Features:**
  - Timeout handling with retry logic (up to 3 attempts)
  - Skips root categories (ID 1 & 2)
  - Progress logging every 5 categories
  - Detailed error tracking and reporting
  - Limit of 50 categories per import to avoid server overload
  - Small delays (0.3s) between requests to reduce server load

### `action_import_products()`
- **Purpose:** Import products from Prestashop with comprehensive data mapping
- **Features:**
  - Timeout handling with retry logic (up to 3 attempts)
  - Extracts product name, price, reference, description, category, active status
  - Price validation and error handling
  - Category association with existing Odoo categories
  - Duplicate detection (by name or reference)
  - Progress logging every 5 products
  - Limit of 30 products per import to avoid server overload
  - Small delays (0.5s) between requests to reduce server load

## Benefits of Separation

1. **Flexibility:** Users can now import categories and products independently
2. **Performance:** Smaller batches reduce server load and timeout risks
3. **Debugging:** Easier to troubleshoot specific import issues
4. **User Experience:** Better control over import process
5. **Error Isolation:** Problems with one type don't affect the other

## Usage Instructions

1. **Test Connection First:** Always use "Test Connection" before importing
2. **Import Categories First:** Categories should be imported before products for proper category association
3. **Import Products:** Import products after categories are in place
4. **Monitor Progress:** Check Odoo logs for detailed progress information

## Technical Notes

- Both functions use the same robust error handling and retry logic
- Progress is logged to help monitor large imports
- Server-friendly delays prevent overwhelming slow Prestashop servers
- All imports include comprehensive validation and duplicate checking
- Notification system provides immediate feedback on import results
