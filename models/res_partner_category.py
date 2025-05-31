# -*- coding: utf-8 -*-
from odoo import models, fields

class ResPartnerCategory(models.Model):
    _inherit = 'res.partner.category'

    x_prestashop_group_id = fields.Integer(
        string='Prestashop Group ID',
        readonly=True,
        copy=False,
        help="The ID of this customer group in Prestashop. Used for mapping during import."
    )
