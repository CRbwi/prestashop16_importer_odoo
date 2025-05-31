# -*- coding: utf-8 -*-
from odoo import models, fields

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_prestashop_product_id = fields.Integer(
        string='Prestashop Product ID',
        readonly=True,
        copy=False,
        help="The ID of this product in Prestashop. Used for mapping during import."
    )
    
    # Fields to ensure website compatibility
    is_published = fields.Boolean(
        string='Published on Website',
        default=True,
        help="Whether this product is published on the website"
    )
    
    # Website-specific description field for Odoo 18
    website_description = fields.Html(
        string='Website Description',
        help="Description shown on the website product page"
    )
