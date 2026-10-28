import frappe


def execute():
	"""Seed Portal Listing state from currently published properties per source."""
	sources = frappe.get_all(
		"Integration Source",
		filters={"operation_type": "Export", "enabled": 1},
		fields=["name", "target_doctype", "transfer_scope", "export_filters_json",
				"publish_field", "status_field", "last_sync_at"],
	)

	for source in sources:
		_seed_source(frappe.get_doc("Integration Source", source["name"]))

	frappe.db.commit()


def _seed_source(source):
	from openimmo_propms.services.export_engine import _get_records_for_export
	from openimmo_propms.services.quality_gate import evaluate_quality_gate_for_export

	existing_props = {
		row["property"]
		for row in frappe.get_all(
			"Portal Listing",
			filters={"source": source.name},
			fields=["property"],
		)
	}

	try:
		records = _get_records_for_export(source, {}, strip_modified=True)
		records, _ = evaluate_quality_gate_for_export(source, records)
	except Exception as e:
		frappe.logger("openimmo_patch").warning(
			f"seed_portal_listings: skipping {source.name} — {e}"
		)
		return

	inserted = 0
	for record in records:
		prop_name = record.get("name")
		if not prop_name or prop_name in existing_props:
			continue

		obid = (
			record.get("custom_openimmo_obid")
			or record.get("openimmo_obid")
			or prop_name
		)

		frappe.get_doc({
			"doctype": "Portal Listing",
			"source": source.name,
			"property_doctype": source.target_doctype or "Property",
			"property": prop_name,
			"obid": obid,
			"state": "Online",
			"last_action": "CHANGE",
		}).insert(ignore_permissions=True, ignore_links=True)
		inserted += 1

	frappe.logger("openimmo_patch").info(
		f"seed_portal_listings: {source.name} — inserted {inserted} records"
	)
