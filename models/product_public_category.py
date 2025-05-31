# -*- coding: utf-8 -*-
from odoo import models, fields

class ProductPublicCategory(models.Model):
    _inherit = 'product.public.category'

    x_prestashop_category_id = fields.Integer(
        string='Prestashop Category ID',
        readonly=True,
        copy=False,
        help="The ID of this category in Prestashop. Used for mapping during import."
    )
