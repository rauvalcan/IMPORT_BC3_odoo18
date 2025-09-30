# -*- coding: utf-8 -*-

import base64
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import chardet
except ImportError:
    _logger.debug("Cannot `import chardet`.")


class BC3ImportWizard(models.TransientModel):
    _name = "bc3.import.wizard"
    _description = "BC3 Import Wizard"

    bc3_file = fields.Binary(string="BC3 File", required=True)
    bc3_filename = fields.Char()

    def _get_data_from_file(self):
        """
        Read the file and get the data.
        """
        self.ensure_one()
        if not self.bc3_file:
            raise UserError(_("Please, upload a BC3 file."))
        
        data = base64.b64decode(self.bc3_file)
        
        try:
            # Forzamos la decodificación que corresponde a ANSI en sistemas españoles.
            lines = data.decode('windows-1252').splitlines()
        except UnicodeDecodeError:
            # Si falla, como plan B, intentamos con UTF-8.
            try:
                lines = data.decode('utf-8').splitlines()
            except UnicodeDecodeError:
                raise UserError(_("The file encoding could not be determined. Please save it as UTF-8 or Windows-1252 (ANSI)."))

        return lines

    def _prepare_concepts_dict(self, lines, version_id):
        """
        Prepare a dictionary with the concepts of the file.
        This is the final, corrected version.
        """
        concepts = {}
        DELIMITER = "|"

        for line_raw in lines:
            line = line_raw.strip()

            if not line.startswith("~C" + DELIMITER):
                continue

            # --- CORRECCIÓN CLAVE ---
            # Quitamos los 3 primeros caracteres ('~C|') para empezar con el código.
            data_line = line[3:]
            
            try:
                parts = data_line.split(DELIMITER)
                
                if len(parts) >= 4:
                    code = parts[0]
                    uom = parts[1]
                    description = parts[2]
                    price_str = parts[3]
                    
                    if not code:
                        continue

                    price = float(price_str.replace(",", ".")) if price_str else 0.0
                    quantity = 1.0

                    concepts[code] = {
                        "description": description,
                        "uom": uom,
                        "price": price,
                        "quantity": quantity,
                        "version_id": version_id.id,
                    }
                else:
                    _logger.warning(f"Concept line ignored due to incorrect format: {line}")

            except (ValueError, IndexError) as e:
                _logger.error(f"Error processing line '{line}': {e}")
                continue
        
        return concepts

    def _prepare_sale_order_line_from_concept(self, concept, sale_order):
        """Prepare the values for the sale order line."""
        product_uom = self.env["uom.uom"].search([("name", "=", concept["uom"])], limit=1)
        if not product_uom:
            product_uom = self.env.ref("uom.product_uom_unit")

        # Buscamos el producto por la descripción, que es más legible que el código.
        # Puedes cambiar 'default_code' por otro campo si lo prefieres.
        product = self.env["product.product"].search(
            [("default_code", "=", concept["description"])], limit=1
        )
        if not product:
            product = self.env["product.product"].create(
                {
                    "name": concept["description"],
                    "default_code": concept["description"],
                    "type": "service",
                    "uom_id": product_uom.id,
                    "uom_po_id": product_uom.id,
                    "list_price": concept["price"],
                }
            )
        return {
            "order_id": sale_order.id,
            "product_id": product.id,
            "product_uom_qty": concept["quantity"],
            "price_unit": concept["price"],
            "name": concept["description"],
            "product_uom": product_uom.id,
        }

    def _prepare_sale_order_vals(self, version_id):
        """Prepare the values for the sale order."""
        return {
            "name": self.bc3_filename or _("Imported BC3"),
            "bc3_version_id": version_id.id,
        }

    def _create_sale_order_and_lines(self, concepts, version_id):
        """Create the sale order and the lines."""
        if not concepts:
            raise UserError(_("No valid concepts were found in the BC3 file to import."))
            
        sale_order_vals = self._prepare_sale_order_vals(version_id)
        sale_order = self.env["sale.order"].create(sale_order_vals)
        
        lines_vals_list = []
        for concept in concepts.values():
            sale_order_line_vals = self._prepare_sale_order_line_from_concept(
                concept, sale_order
            )
            lines_vals_list.append(sale_order_line_vals)
        
        if lines_vals_list:
            self.env["sale.order.line"].create(lines_vals_list)
            
        return sale_order

    def action_import(self):
        """Process the file and create the sale order."""
        self.ensure_one()
        version_id = self.env["bc3.version"].create({"name": self.bc3_filename or "BC3 Import"})
        lines = self._get_data_from_file()
        concepts = self._prepare_concepts_dict(lines, version_id)
        sale_order = self._create_sale_order_and_lines(concepts, version_id)
        
        action = self.env["ir.actions.actions"]._for_xml_id(
            "sale.action_quotations_with_onboarding"
        )
        action["views"] = [(self.env.ref("sale.view_order_form").id, "form")]
        action["res_id"] = sale_order.id
        return action