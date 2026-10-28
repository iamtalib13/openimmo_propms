# Copyright (c) 2025, Aakvatech and contributors
# For license information, please see license.txt

import os

import frappe
import xmlschema
from lxml import etree


class XSDDrivenBuilder:
	def __init__(self, xsd_name="openimmo_127c.xsd"):
		self.xsd_path = frappe.get_app_path("openimmo_propms", "templates", "xsd", xsd_name)
		if not os.path.exists(self.xsd_path):
			frappe.throw(f"XSD file not found at {self.xsd_path}")

		self.schema = xmlschema.XMLSchema(self.xsd_path)

	def generate_xml(self, data_map, anbieter_context, version="1.2.7"):
		"""
		data_map: list of dicts, each dict is mapped property data
		anbieter_context: dict with parent-level fields (anbieter_id, portal_name, etc.)
		"""
		# DEBUG: Print the data map
		print("DEBUG: Property Export Data Map:", data_map)

		# 1. Create root based on schema
		root = etree.Element("openimmo")

		# 2. Add uebertragung
		self._build_uebertragung(
			root,
			anbieter_context.get("portal_name"),
			anbieter_context.get("transfer_scope"),
			anbieter_context.get("transfer_mode"),
			version,
		)

		# 3. Add anbieter
		anbieter = etree.SubElement(root, "anbieter")

		# Mandatory fields: raise error if missing to maintain data integrity
		firma = anbieter_context.get("firma")
		openimmo_anid = anbieter_context.get("openimmo_anid")

		if not firma:
			frappe.throw("Mandatory field 'firma' is missing in provider context.")
		if not openimmo_anid:
			frappe.throw("Mandatory field 'openimmo_anid' is missing in provider context.")

		etree.SubElement(anbieter, "anbieternr").text = str(anbieter_context.get("anbieter_id", ""))
		etree.SubElement(anbieter, "firma").text = firma
		etree.SubElement(anbieter, "openimmo_anid").text = openimmo_anid

		# 4. Add properties (immobilie)
		immobilie_schema = self.schema.elements["immobilie"]
		for prop_data in data_map:
			immobilie_node = etree.SubElement(anbieter, "immobilie")
			if prop_data.get("__action__") == "DELETE":
				self._build_minimal_delete_immobilie(immobilie_node, prop_data, anbieter_context)
			else:
				self._fill_element(immobilie_node, immobilie_schema, prop_data)

		return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode("utf-8")

	def _build_minimal_delete_immobilie(self, immobilie_node, prop_data, anbieter_context):
		"""Build minimal XSD-valid immobilie node for DELETE action."""
		# objektkategorie
		objektkategorie = etree.SubElement(immobilie_node, "objektkategorie")
		etree.SubElement(
			objektkategorie,
			"nutzungsart",
			WOHNEN="true",
			GEWERBE="false",
			ANLAGE="false",
			WAZ="false",
		)
		etree.SubElement(
			objektkategorie,
			"vermarktungsart",
			KAUF="false",
			MIETE_PACHT="true",
		)
		objektart = etree.SubElement(objektkategorie, "objektart")
		etree.SubElement(objektart, "wohnung")

		# geo
		geo = etree.SubElement(immobilie_node, "geo")
		ort = etree.SubElement(geo, "ort")
		ort.text = prop_data.get("geo.ort") or "Berlin"

		# kontaktperson
		kontaktperson = etree.SubElement(immobilie_node, "kontaktperson")
		email = etree.SubElement(kontaktperson, "email_direkt")
		email.text = anbieter_context.get("contact_email") or "info@example.com"
		name = etree.SubElement(kontaktperson, "name")
		name.text = anbieter_context.get("firma") or "Admin"

		# verwaltung_techn
		verwaltung_techn = etree.SubElement(immobilie_node, "verwaltung_techn")
		obid = prop_data.get("verwaltung_techn.openimmo_obid") or prop_data.get("objektnr_intern") or "NO-OBID"
		objektnr_extern = etree.SubElement(verwaltung_techn, "objektnr_extern")
		objektnr_extern.text = obid
		etree.SubElement(verwaltung_techn, "aktion", aktionart="DELETE")
		openimmo_obid = etree.SubElement(verwaltung_techn, "openimmo_obid")
		openimmo_obid.text = obid
		stand_vom = etree.SubElement(verwaltung_techn, "stand_vom")
		from frappe.utils import nowdate
		stand_vom.text = nowdate()

	def _build_uebertragung(self, root, portal_name, transfer_scope, transfer_mode, version):
		from frappe.utils import now_datetime

		ueb = etree.SubElement(root, "uebertragung")
		ueb.set("art", "ONLINE")
		if transfer_scope:
			ueb.set("umfang", transfer_scope)
		if transfer_mode:
			ueb.set("modus", transfer_mode)
		ueb.set("version", version)
		ueb.set("sendersoftware", "OIGEN")
		ueb.set("senderversion", "1.0")
		ueb.set("timestamp", now_datetime().strftime("%Y-%m-%dT%H:%M:%S"))
		# Removed 'portal' as it's not in standard XSD

	def _fill_element(self, node, schema_element, data, prefix=""):
		"""
		Recursively fill element based on schema hierarchy.
		"""
		from frappe.utils import nowdate

		# 1. Handle Attributes
		for attr_name, attr in schema_element.attributes.items():
			attr_path = f"{prefix}@{attr_name}" if prefix else f"@{attr_name}"
			val = data.get(attr_path)

			# Special handling for mandatory user_defined fields
			if val in [None, ""] and attr.use == "required":
				if schema_element.name == "user_defined_simplefield" and attr_name == "feldname":
					parts = prefix.split(".")
					val = parts[-1] if parts else "default"

			if val not in [None, ""]:
				node.set(attr_name, self._format_value(val))

		# 2. Handle Complex Content (Children)
		if hasattr(schema_element.type, "content") and schema_element.type.content:
			self._fill_group(node, schema_element.type.content, data, prefix)

		# 3. Handle Simple Content (Text)
		if schema_element.type.is_simple() or schema_element.type.has_simple_content():
			val = data.get(prefix)

			# Mandatory field 'stand_vom' needs a date (YYYY-MM-DD)
			if schema_element.name == "stand_vom":
				from frappe.utils import getdate

				if val in [None, ""]:
					val = nowdate()
				else:
					# Ensure it's a date object (handles strings and datetime objects)
					val = getdate(val)

			if val not in [None, ""]:
				node.text = self._format_value(val)
			elif schema_element.min_occurs > 0 and not self._is_in_choice(schema_element) and not node.text:
				node.text = ""

	def _format_value(self, val):
		if val in [None, ""]:
			return None
		if isinstance(val, bool):
			return "true" if val else "false"
		return str(val)

	def _fill_group(self, node, group, data, prefix):
		"""Processes a sequence or choice group."""
		if group.model == "choice":
			# Data-Driven selection: Only fill if data exists
			for branch in group:
				if self._has_data_recursive(branch, data, prefix):
					self._fill_item(node, branch, data, prefix)
					break

		else:  # sequence or all
			for item in group:
				self._fill_item(node, item, data, prefix)

	def _fill_item(self, node, item, data, prefix, force_mandatory=False):
		"""Processes an item (Element or Group) inside a group."""
		if isinstance(item, xmlschema.validators.XsdElement):
			child_name = item.name
			child_prefix = f"{prefix}.{child_name}" if prefix else child_name

			# Check for repeatability
			indices = self._get_indices(data, child_prefix)
			if indices:
				for idx in indices:
					indexed_prefix = f"{child_prefix}.{idx}"
					child_node = etree.SubElement(node, child_name)
					self._fill_element(child_node, item, data, indexed_prefix)
			else:
				has_data = self._has_data_recursive(item, data, prefix)
				# Ensure mandatory sequence elements are created even without data
				is_mandatory = item.min_occurs > 0 and not self._is_in_choice(item)

				if has_data or is_mandatory or force_mandatory:
					child_node = etree.SubElement(node, child_name)
					self._fill_element(child_node, item, data, child_prefix)
		else:
			# Nested Group
			self._fill_group(node, item, data, prefix)

	def _has_data_recursive(self, item, data, prefix):
		"""Checks if an element or group has any mapped data recursively."""
		if isinstance(item, xmlschema.validators.XsdElement):
			child_prefix = f"{prefix}.{item.name}" if prefix else item.name
			return any(
				k == child_prefix or k.startswith(child_prefix + ".") or k.startswith(child_prefix + "@")
				for k in data
			)
		else:
			# Group: check all children
			return any(self._has_data_recursive(child, data, prefix) for child in item)

	def _is_in_choice(self, schema_element):
		"""Check if an element is part of a choice branch."""
		curr = schema_element.parent
		while curr and not isinstance(curr, xmlschema.validators.XsdComplexType):
			if getattr(curr, "model", None) == "choice":
				return True
			if hasattr(curr, "parent"):
				curr = curr.parent
			else:
				break
		return False

	def _get_indices(self, data, prefix):
		"""Find numeric indices in data keys for a given prefix."""
		indices = set()
		prefix_dot = prefix + "."
		for k in data.keys():
			if k.startswith(prefix_dot):
				suffix = k[len(prefix_dot) :]
				parts = suffix.split(".")
				if parts[0].isdigit():
					indices.add(int(parts[0]))
		return sorted(list(indices))


def generate_xsd_based_xml(properties_data, anbieter_context, version="1.2.7"):
	builder = XSDDrivenBuilder()
	return builder.generate_xml(properties_data, anbieter_context, version)
