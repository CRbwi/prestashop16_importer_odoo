{
    'name': 'Prestashop 1.6 Importer',
    'version': '18.0.2.1.0',
    'category': 'Extra Tools',
    'summary': 'Import data from Prestashop 1.6 (Customers, Addresses, Products, Categories, Customer Groups as Pricelists)',
    'description': '''
Prestashop 1.6 Importer for Odoo 18
====================================

This module allows you to import data from Prestashop 1.6 stores using the webservice API.

Features:
---------
* Import customers and their addresses
* Import products with categories
* Import product categories
* Import customer groups as pricelists
* Comprehensive connection testing with diagnostics
* Works with direct HTTP requests (fallback for prestapyt issues)

Requirements:
-------------
* Prestashop 1.6 with webservice enabled
* Valid API key with appropriate permissions
* Python requests library (usually pre-installed)
* Optional: prestapyt library for enhanced functionality

Usage:
------
1. Go to Settings > Technical > Prestashop 1.6 Importer
2. Create a new backend configuration
3. Fill in your Prestashop URL and API key
4. Test the connection
5. Use the import buttons to import your data

For support and issues, check the connection diagnostics first.
    ''',
    'author': 'GitHub Copilot',
    'license': 'AGPL-3',
    'depends': [
        'base',
        'product',  # For products, categories, and pricelists
        'sale_management',  # For pricelists specifically
        'account',  # For partners (customers)
    ],
    'external_dependencies': {
        'python': ['requests'],  # Primary dependency - usually pre-installed
    },
    'data': [
        'security/ir.model.access.csv',
        'views/prestashop_backend_views.xml',
        'views/prestashop_importer_menu.xml',
    ],
    'installable': True,
    'application': True,
}
