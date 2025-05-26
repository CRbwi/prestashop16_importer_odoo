# Prestashop 1.6 Importer for Odoo 18

This module allows you to import data from Prestashop 1.6 stores into Odoo 18 using the Prestashop webservice API.

## Features

- **Customer Import**: Import customers with their basic information (name, email)
- **Product Import**: Import products with prices, descriptions, and references
- **Category Import**: Import product categories and link them to products
- **Customer Groups**: Import customer groups as price lists with discount rates
- **Connection Testing**: Comprehensive diagnostic tools to troubleshoot API connectivity

## Requirements

### Prestashop 1.6 Setup
1. Enable webservice in Prestashop Admin: Advanced Parameters > Webservice
2. Create an API key with permissions for:
   - customers (read)
   - products (read)
   - categories (read)
   - groups (read)
   - shops (read)
   - languages (read)

### Odoo Setup
- Odoo 18
- Python `requests` library (usually pre-installed)
- Optional: `prestapyt` library for enhanced functionality

## Installation

1. Copy this addon to your Odoo addons directory
2. Update app list in Odoo
3. Install "Prestashop 1.6 Importer"

## Configuration

1. Go to **Settings > Technical > Prestashop 1.6 Importer**
2. Click **Create** to add a new backend
3. Fill in:
   - **Name**: A descriptive name for your configuration
   - **Prestashop URL**: Your store URL (e.g., `https://yourstore.com`)
   - **API Key**: Your webservice API key from Prestashop

## Usage

### Testing Connection
1. Open your backend configuration
2. Click **Test Connection** to run comprehensive diagnostics
3. If issues are found, use **Test URL Only** for basic connectivity testing

### Importing Data
Use the import buttons on your backend configuration:
- **Import Customers**: Imports all customers from Prestashop
- **Import Products & Categories**: Imports categories first, then products
- **Import Customer Groups**: Imports customer groups as price lists

## Troubleshooting

### Connection Issues

**Problem**: URL not accessible
- Check if Prestashop is running
- Verify the URL is correct
- Try accessing `yourstore.com/api` in browser

**Problem**: Authentication fails
- Verify API key is correct
- Check API key permissions in Prestashop
- Ensure webservice is enabled

**Problem**: Returns HTML instead of XML
- Check `.htaccess` file in Prestashop root
- Add this rule if missing:
  ```
  RewriteRule ^api/?(.*)$ webservice/dispatcher.php?url=$1 [QSA,L]
  ```

### Import Issues

**Problem**: No data imported
- Check if data exists in Prestashop
- Verify API key has read permissions
- Look at Odoo logs for detailed error messages

**Problem**: Prestapyt library issues
- The addon works with direct HTTP requests as fallback
- This is normal and expected behavior

## Technical Details

### Implementation
- Uses direct HTTP requests with XML parsing for reliability
- Falls back from prestapyt when authentication conflicts occur
- Comprehensive error handling and user feedback

### Data Mapping
- **Customers** → `res.partner` (with `customer_rank=1`)
- **Products** → `product.template`
- **Categories** → `product.category`
- **Customer Groups** → `product.pricelist` (with discount rates)

### Duplicate Handling
- Checks for existing records by email (customers) or name (products/categories)
- Skips duplicates during import
- Logs all creation activities

## Support

For issues or questions:
1. Check the connection diagnostics first
2. Review Odoo server logs for detailed error messages
3. Verify Prestashop webservice configuration
4. Test API endpoints manually if needed

## License

AGPL-3 License

## Version History

- **18.0.2.1.0**: Complete implementation with direct HTTP requests
- **18.0.2.0.0**: Initial Odoo 18 compatible version
