# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo.tools.float_utils import float_compare

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.addons.mrp_subcontracting.models.stock_picking import StockPicking as OrigStockPicking


def _action_done(self):
    res = super(OrigStockPicking, self)._action_done()
    for move in self.move_ids.filtered(lambda move: move.is_subcontract):
        # Auto set qty_producing/lot_producing_id of MO wasn't recorded
        # manually (if the flexible + record_component or has tracked component)
        productions = move._get_subcontract_production()
        recorded_productions = productions.filtered(lambda p: p._has_been_recorded())
        recorded_qty = sum(recorded_productions.mapped('qty_producing'))
        sm_done_qty = sum(productions._get_subcontract_move().mapped('quantity_done'))
        rounding = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        if float_compare(recorded_qty, sm_done_qty, precision_digits=rounding) >= 0:
            continue
        production = productions - recorded_productions
        if not production:
            continue
        if len(production) > 1:
            raise UserError(_("There shouldn't be multiple productions to record for the same subcontracted move."))
        # Manage additional quantities
        quantity_done_move = move.product_uom._compute_quantity(move.quantity_done, production.product_uom_id)
        if float_compare(production.product_qty, quantity_done_move,
                         precision_rounding=production.product_uom_id.rounding) == -1:
            change_qty = self.env['change.production.qty'].create({
                'mo_id': production.id,
                'product_qty': quantity_done_move
            })
            change_qty.with_context(skip_activity=True).change_prod_qty()
        # Create backorder MO for each move lines
        amounts = [move_line.qty_done for move_line in move.move_line_ids]
        len_amounts = len(amounts)
        productions = production._split_productions({production: amounts})
        for production, move_line in zip(productions, move.move_line_ids):
            if move_line.lot_id:
                production.lot_producing_id = move_line.lot_id
            production.qty_producing = production.product_qty
            production._set_qty_producing()
        productions[:len_amounts].subcontracting_has_been_recorded = True

    for picking in self:
        productions_to_done = picking._get_subcontract_production()._subcontracting_filter_to_done()
        if not productions_to_done:
            continue
        productions_to_done = productions_to_done.sudo()
        production_ids_backorder = []
        if not self.env.context.get('cancel_backorder'):
            production_ids_backorder = productions_to_done.filtered(lambda mo: mo.state == "progress").ids
        productions_to_done.with_context(mo_ids_to_backorder=production_ids_backorder).button_mark_done()
        # For concistency, set the date on production move before the date
        # on picking. (Traceability report + Product Moves menu item)
        minimum_date = min(picking.move_line_ids.mapped('date'))
        production_moves = productions_to_done.move_raw_ids | productions_to_done.move_finished_ids
        production_moves.write({'date': minimum_date - timedelta(seconds=1)})
        production_moves.move_line_ids.write({'date': minimum_date - timedelta(seconds=1)})
    return res
OrigStockPicking._action_done = _action_done
