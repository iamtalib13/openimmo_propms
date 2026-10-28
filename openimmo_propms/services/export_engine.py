import ftplib
import hashlib
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_url
from frappe.utils.file_manager import save_file_on_filesystem

from openimmo_propms.services.export_mapper import build_property_data
from openimmo_propms.services.immowelt_xml_creator import build_immowelt_document
from openimmo_propms.services.quality_gate import (
	evaluate_quality_gate_for_export,
	validate_quality_gate,
)
from openimmo_propms.services.validator import validate_xml_against_xsd
from openimmo_propms.services.xml_builder import (
	build_openimmo_document,
	render_xml_template,
)
from openimmo_propms.services.xsd_builder import generate_xsd_based_xml

_OPENIMMO_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "xml" / "openimmo-export.xml"


def run_export(source_name, **kwargs):
	"""Run a metadata-driven export without touching the import flow."""
	run_started = frappe.utils.now()
	try:
		source = frappe.get_doc("Integration Source", source_name)
		_validate_export_source(source)

		is_teil = (source.transfer_scope or "").upper() == "TEIL"
		if is_teil:
			records = _get_records_for_export(source, kwargs, strip_modified=True)
			records, blocked = evaluate_quality_gate_for_export(source, records)
			skipped_records = len(blocked)

			# O = Online listings for this source
			online_listings = frappe.get_all(
				"Portal Listing",
				filters={"source": source.name, "state": "Online"},
				fields=["name", "property", "obid"],
			)
			online_map = {l["property"]: l for l in online_listings}
			online_prop_names = set(online_map.keys())
			target_prop_names = {r["name"] for r in records}

			last_sync_at = source.last_sync_at
			# change = {T where modified > last_sync_at} union (T - O)
			change_records = []
			for r in records:
				if r["name"] not in online_prop_names:
					change_records.append(r)
				elif not last_sync_at:
					change_records.append(r)
				elif r.get("modified") and frappe.utils.get_datetime(
					r["modified"]
				) > frappe.utils.get_datetime(last_sync_at):
					change_records.append(r)

			# delete = O - T
			delete_prop_names = online_prop_names - target_prop_names
			delete_listings = [online_map[p] for p in delete_prop_names]

			if not change_records and not delete_listings:
				if frappe.job:
					source.db_set("last_sync_status", "Success (No modifications)")
					source.db_set("last_sync_at", run_started)
					return {
						"status": "success",
						"record_count": 0,
						"skipped_records": skipped_records,
						"blocked_records": blocked,
						"skipped": len(blocked),
						"message": "No changes or deletions to export.",
					}
				else:
					frappe.throw(_("No changes or deletions found matching export criteria."))

			# Build mapped records for changes
			mapped_records = []
			active_records_to_sync = []
			for record in change_records:
				mapped = build_property_data(source, record)
				mapped["__action__"] = "CHANGE"
				mapped["verwaltung_techn.aktion"] = "CHANGE"
				mapped["verwaltung_techn.aktion@aktionart"] = "CHANGE"
				if not mapped.get("verwaltung_techn.openimmo_obid"):
					mapped["verwaltung_techn.openimmo_obid"] = mapped.get(
						"objektnr_intern", record.get("name")
					)
				mapped_records.append(mapped)
				active_records_to_sync.append((record["name"], mapped["verwaltung_techn.openimmo_obid"]))

			# Build minimal records for deletes
			delete_records_data = []
			for l in delete_listings:
				del_doc = frappe._dict(
					{
						"name": l["property"],
						"custom_property_name": l["property"],
						"modified": frappe.utils.now_datetime(),
					}
				)
				delete_records_data.append(del_doc)
				del_mapped = {
					"__action__": "DELETE",
					"verwaltung_techn.aktion": "DELETE",
					"verwaltung_techn.aktion@aktionart": "DELETE",
					"verwaltung_techn.openimmo_obid": l["obid"],
					"objektnr_intern": l["obid"],
					"objektnr_extern": l["obid"],
				}
				mapped_records.append(del_mapped)

			export_all_records = change_records + delete_records_data
			deleted_count = len(delete_listings)
			deleted_to_sync = [(l["property"], l["obid"]) for l in delete_listings]
		else:
			records = _get_records_for_export(source, kwargs)
			records, blocked = evaluate_quality_gate_for_export(source, records)
			skipped_records = len(blocked)
			if not records:
				if frappe.job:
					source.db_set("last_sync_status", "Success (No modifications)")
					source.db_set("last_sync_at", run_started)
					return {
						"status": "success",
						"record_count": 0,
						"skipped_records": skipped_records,
						"blocked_records": blocked,
						"skipped": len(blocked),
						"message": "No properties found to export.",
					}
				else:
					frappe.throw(_("No properties found matching the configured export filters."))

			transfer_mode = source.transfer_mode or "CHANGE"
			mapped_records = []
			for record in records:
				mapped = build_property_data(source, record)
				mapped["__action__"] = transfer_mode
				mapped["verwaltung_techn.aktion"] = transfer_mode
				mapped["verwaltung_techn.aktion@aktionart"] = transfer_mode
				if not mapped.get("verwaltung_techn.openimmo_obid"):
					mapped["verwaltung_techn.openimmo_obid"] = mapped.get("objektnr_intern", "NO-OBID")
				mapped_records.append(mapped)

			export_all_records = records
			deleted_count = 0
			active_records_to_sync = [
				(r["name"], mapped["verwaltung_techn.openimmo_obid"])
				for r, mapped in zip(records, mapped_records, strict=False)
			]
			deleted_to_sync = []

		anbieter_id = kwargs.get("anbieter_id") or source.anbieter_id

		# Check if we should use XSD-driven generation
		if source.export_format == "OpenImmo" and not getattr(source, "use_jinja_template", 0):
			anbieter_context = {
				"anbieter_id": anbieter_id,
				"firma": getattr(source, "provider_name", "") or "My Company",
				"openimmo_anid": getattr(source, "openimmo_anid", ""),
				"portal_name": source.portal_name,
				"transfer_scope": source.transfer_scope,
				"transfer_mode": source.transfer_mode,
			}

			xml_content = generate_xsd_based_xml(mapped_records, anbieter_context)

			# Validation as final check
			is_valid, error_msg = validate_xml_against_xsd(xml_content, "openimmo_127c.xsd")
			if not is_valid:
				frappe.throw(_("Generated XML is not compliant with XSD: {0}").format(error_msg))

			all_image_urls = []
			if cint(getattr(source, "send_as_zip", 0)):
				seen = set()
				for record in export_all_records:
					for url in _collect_image_urls(source, record):
						if url not in seen:
							all_image_urls.append(url)
							seen.add(url)

			documents = [
				{
					"filename": _build_batch_filename(source),
					"xml_content": _normalize_xml_document(xml_content),
					"record_count": len(export_all_records),
					"deleted_records": deleted_count,
					"image_urls": all_image_urls,
				}
			]
		else:
			documents = _build_export_documents(
				source, list(zip(export_all_records, mapped_records, strict=False)), kwargs
			)
			for doc_item in documents:
				doc_item["deleted_records"] = deleted_count

			# Validate Jinja-rendered OpenImmo XML against XSD
			if source.export_format == "OpenImmo" and getattr(source, "use_jinja_template", 0):
				for document in documents:
					is_valid, error_msg = validate_xml_against_xsd(
						document["xml_content"], "openimmo_127c.xsd"
					)
					if not is_valid:
						frappe.throw(_("Generated XML is not compliant with XSD: {0}").format(error_msg))

		should_save = _should_save_file(source, kwargs.get("save_file"))
		responses = []

		for document in documents:
			xml_hash = _build_xml_hash(document["xml_content"])

			if cint(getattr(source, "send_as_zip", 0)):
				upload_filename = document["filename"].rsplit(".", 1)[0] + ".zip"
				upload_content = _build_zip_package(
					document["filename"], document["xml_content"], document.get("image_urls")
				)
			else:
				upload_filename = document["filename"]
				upload_content = document["xml_content"].encode("utf-8")

			skipped_for_job = skipped_records if not responses else 0
			response = {
				"status": "success",
				"record_count": document["record_count"],
				"deleted_records": document.get("deleted_records", 0),
				"total_records": document["record_count"] + skipped_for_job,
				"skipped_records": skipped_for_job,
				"blocked_records": blocked if not responses else [],
				"skipped": len(blocked if not responses else []),
				"filename": upload_filename,
				"xml_hash": xml_hash,
			}

			if should_save:
				file_doc = _save_export_file(upload_filename, upload_content, source.name)
				response["file_url"] = file_doc.file_url
			else:
				response["xml"] = document["xml_content"]

			delivery_result = _deliver_export(source, upload_filename, upload_content, xml_hash)
			if delivery_result:
				response.update(delivery_result)

			job = _create_export_job(source, response)
			response["job_name"] = job.name

			# Successful delivery: upsert Portal Listing state
			if response.get("delivery_status") != "failed":
				_persist_portal_listings_state(
					source.name,
					source.target_doctype or "Property",
					active_records_to_sync,
					deleted_to_sync,
					job.name,
					run_started,
				)

			responses.append(response)

		summary = _summarize_export_response(source, responses)

		# Update last sync time and status for all successful runs
		source.db_set("last_sync_at", run_started)
		if source.source_type != "FTP" or not cint(source.ftp_transfer_enabled):
			source.db_set("last_sync_status", "Export completed")

		return summary
	except Exception as e:
		frappe.logger("openimmo_export").error(f"Export failed for {source_name}: {e!s}")
		raise e


def _persist_portal_listings_state(
	source_name, target_doctype, active_records, deleted_records, job_name, run_started
):
	"""Upsert Portal Listing records after successful export delivery."""
	for prop_name, obid in active_records:
		listing_name = f"{source_name}-{prop_name}"
		if frappe.db.exists("Portal Listing", listing_name):
			frappe.db.set_value(
				"Portal Listing",
				listing_name,
				{
					"obid": obid,
					"state": "Online",
					"last_action": "CHANGE",
					"last_sent_at": run_started,
					"last_job": job_name,
				},
			)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Portal Listing",
					"source": source_name,
					"property_doctype": target_doctype,
					"property": prop_name,
					"obid": obid,
					"state": "Online",
					"last_action": "CHANGE",
					"last_sent_at": run_started,
					"last_job": job_name,
				}
			)
			doc.flags.ignore_links = True
			doc.insert(ignore_permissions=True)

	for prop_name, _obid in deleted_records:
		listing_name = f"{source_name}-{prop_name}"
		if frappe.db.exists("Portal Listing", listing_name):
			frappe.db.set_value(
				"Portal Listing",
				listing_name,
				{
					"state": "Withdrawn",
					"last_action": "DELETE",
					"last_sent_at": run_started,
					"last_job": job_name,
				},
			)


def _validate_export_source(source):
	if source.operation_type != "Export":
		frappe.throw(_("Integration Source must be configured for Export"))

	if source.export_format not in ("OpenImmo", "Immowelt"):
		frappe.throw(_("Only OpenImmo and Immowelt export are supported in this version"))

	if not source.field_mappings and not getattr(source, "use_jinja_template", 0):
		frappe.throw(_("Please configure at least one field mapping"))

	if not source.target_doctype:
		frappe.throw(_("Target DocType is required for export"))

	if (
		source.export_format == "OpenImmo"
		and not getattr(source, "use_jinja_template", 0)
		and source.xml_template
		and "{{record_blocks}}" not in source.xml_template
	):
		frappe.throw(_("XML Template must include the {{record_blocks}} placeholder"))


def _deliver_export(source, filename, content_bytes, xml_hash):
	if source.source_type == "FTP" and cint(source.ftp_transfer_enabled):
		return _upload_via_ftp(source, filename, content_bytes, xml_hash)

	return {
		"delivery_channel": source.source_type or "Manual Upload",
		"delivery_status": "skipped",
	}


def _build_export_documents(source, export_records, params):
	collect_images = cint(getattr(source, "send_as_zip", 0))

	if source.record_packaging == "Separate XML per Record":
		return [
			{
				"filename": _build_record_filename(source, index + 1),
				"xml_content": _build_xml_content(source, [export_record], params),
				"record_count": 1,
				"image_urls": _collect_image_urls(source, export_record[0]) if collect_images else [],
			}
			for index, export_record in enumerate(export_records)
		]

	all_image_urls = []
	if collect_images:
		seen = set()
		for record, _ in export_records:
			for url in _collect_image_urls(source, record):
				if url not in seen:
					all_image_urls.append(url)
					seen.add(url)

	return [
		{
			"filename": _build_batch_filename(source),
			"xml_content": _build_xml_content(source, export_records, params),
			"record_count": len(export_records),
			"image_urls": all_image_urls,
		}
	]


def _build_xml_content(source, export_records, params):
	anbieter_id = params.get("anbieter_id") or source.anbieter_id

	# Jinja template path - takes precedence when enabled (Notification-style rendering)
	if getattr(source, "use_jinja_template", 0) and source.xml_template:
		return _build_jinja_xml(source, export_records, params)

	if source.export_format == "Immowelt":
		records = [record for record, mapped_record in export_records]
		mapped_records = [mapped_record for record, mapped_record in export_records]
		return _normalize_xml_document(
			build_immowelt_document(
				records,
				mapped_records,
				source=source,
			)
		)

	if source.xml_template:
		return _normalize_xml_document(
			render_xml_template(
				source.xml_template,
				{
					"anbieter_id": anbieter_id,
					"portal_name": source.portal_name or "",
					"transfer_scope": source.transfer_scope or "VOLL",
					"transfer_mode": source.transfer_mode or "NEW",
					"record_blocks": _build_record_blocks(source, export_records),
				},
			)
		)

	if source.export_format == "OpenImmo":
		return _normalize_xml_document(_build_openimmo_template_document(source, export_records, anbieter_id))

	xml_nodes = [_build_xml_node(source, record, mapped_record) for record, mapped_record in export_records]
	return _normalize_xml_document(
		build_openimmo_document(
			anbieter_id,
			xml_nodes,
			portal_name=source.portal_name,
			transfer_scope=source.transfer_scope,
			transfer_mode=source.transfer_mode,
		)
	)


def _build_jinja_xml(source, export_records, params):
	"""Render XML using Jinja template (same pattern as Notification DocType).

	Uses frappe.render_template() which is already available globally.
	Template context provides: doc, mapped, source, frappe.

	For batch mode: template receives all_records list and loops internally.
	For single mode: template receives doc (single record).
	"""
	change_records = [(r, m) for r, m in export_records if m.get("__action__") != "DELETE"]
	delete_records = [(r, m) for r, m in export_records if m.get("__action__") == "DELETE"]

	if source.record_packaging == "Separate XML per Record":
		parts = []
		for record, mapped_record in change_records:
			_validate_single_hero_image(record)
			context = {
				"doc": record,
				"mapped": mapped_record,
				"source": source,
				"frappe": frappe,
			}
			# xml_template is an administrator-only Integration Source field.
			# nosemgrep
			parts.append(frappe.render_template(source.xml_template, context))
		return "\n".join(parts)

	# Batch mode - template gets all_records list for looping
	all_records = []
	for record, mapped_record in change_records:
		_validate_single_hero_image(record)
		if isinstance(record.get("modified"), str):
			record["modified"] = frappe.utils.get_datetime(record["modified"])
		all_records.append({"doc": record, "mapped": mapped_record})

	delete_context = [
		{
			"objektnr_intern": str(m.get("objektnr_intern", "")),
			"objektnr_extern": str(m.get("objektnr_extern", "")),
			"openimmo_obid": str(m.get("verwaltung_techn.openimmo_obid", "")),
		}
		for _, m in delete_records
	]

	context = {
		"all_records": all_records,
		"delete_records": delete_context,
		"doc": frappe._dict({}),
		"source": source,
		"frappe": frappe,
	}
	# xml_template is an administrator-only Integration Source field.
	# nosemgrep
	return _normalize_xml_document(frappe.render_template(source.xml_template, context))


def _validate_single_hero_image(record):
	"""Server-side validation: max 1 is_hero_image per property."""
	gallery = record.get("custom_image_gallery") or []
	if not isinstance(gallery, list):
		return

	hero_count = sum(
		1
		for img in gallery
		if (img.get("is_hero_image") if isinstance(img, dict) else getattr(img, "is_hero_image", 0)) == 1
	)
	if hero_count > 1:
		frappe.throw(
			_("Property {0}: Only 1 hero image allowed, found {1} with is_hero_image=1").format(
				record.get("name", "Unknown"), hero_count
			)
		)


def _normalize_xml_document(xml_content):
	# XML declaration must be the very first content in the document.
	return (xml_content or "").lstrip("\ufeff\r\n\t ")


def _build_xml_hash(xml_content):
	return hashlib.sha256((xml_content or "").encode("utf-8")).hexdigest()


def _export_format_slug(source):
	return (source.export_format or "xml").lower().replace(" ", "_")


def _resolve_filename_placeholders(source, template):
	return template.format(
		anbieter_id=source.anbieter_id or "",
		source_name=source.name or "",
		regi_id=source.regi_id or "",
		openimmo_anid=source.openimmo_anid or "",
	)


def _build_batch_filename(source):
	if source.get("export_file_name"):
		return _resolve_filename_placeholders(source, source.export_file_name)
	return f"{source.name.lower().replace(' ', '_')}_{_export_format_slug(source)}_export.xml"


def _build_record_filename(source, index):
	if source.get("export_file_name"):
		name = _resolve_filename_placeholders(source, source.export_file_name)
		base, _, ext = name.rpartition(".")
		return f"{base}_{index:05d}.{ext}" if base else f"{name}_{index:05d}"
	return f"{source.name.lower().replace(' ', '_')}_{_export_format_slug(source)}_export_{index}.xml"


def _get_records_for_export(source, params, strip_modified=False):
	filters = _get_configured_export_filters(source, strip_modified=strip_modified)
	fieldnames = _sanitize_query_fields(_get_requested_fieldnames(source))
	runtime_filters = {}

	if params.get("filter_company"):
		runtime_filters[_get_required_filter_fieldname(source.company_field, _("Company Field"))] = (
			params.get("filter_company")
		)

	if params.get("property_name"):
		runtime_filters[_get_required_filter_fieldname(source.name_field, _("Name Field"))] = params.get(
			"property_name"
		)

	if params.get("filter_status"):
		runtime_filters[_get_required_filter_fieldname(source.status_field, _("Status Field"))] = params.get(
			"filter_status"
		)

	if params.get("filter_publish") is not None:
		runtime_filters[_get_required_filter_fieldname(source.publish_field, _("Publish Field"))] = cint(
			params.get("filter_publish")
		)

	filters = _merge_filters(filters, runtime_filters)

	records, used_name_only_fallback = _get_all_records(source.target_doctype, filters, fieldnames)

	if _requires_full_doc(source) or used_name_only_fallback:
		return [frappe.get_doc(source.target_doctype, record["name"]).as_dict() for record in records]

	return [frappe._dict(record) for record in records]


def _get_configured_export_filters(source, strip_modified=False):
	filters_json = (source.export_filters_json or "").strip()
	if not filters_json:
		return {}

	# Render Jinja templates in the JSON string
	from frappe.utils import add_days, get_datetime_str, nowdate

	today_str = nowdate()
	yesterday_str = add_days(today_str, -1)

	context = {
		"last_sync_at": get_datetime_str(source.last_sync_at)
		if source.last_sync_at
		else "1970-01-01 00:00:00",
		"today": today_str,
		"today_start": f"{today_str} 00:00:00",
		"today_end": f"{today_str} 23:59:59",
		"yesterday": yesterday_str,
		"yesterday_start": f"{yesterday_str} 00:00:00",
		"yesterday_end": f"{yesterday_str} 23:59:59",
	}

	# filters_json is an administrator-only Integration Source field.
	# nosemgrep
	rendered_json = frappe.render_template(filters_json, context)

	parsed_filters = frappe.parse_json(rendered_json)
	res = {}
	if isinstance(parsed_filters, list):
		res = _normalize_list_filters(parsed_filters)
	elif isinstance(parsed_filters, dict):
		res = dict(parsed_filters)
	else:
		frappe.throw(_("Export Filters (JSON) must be a JSON object or list"))

	if strip_modified:
		if isinstance(res, dict):
			res = {k: v for k, v in res.items() if not (isinstance(k, str) and "modified" in k.lower())}
		elif isinstance(res, list):
			res = [
				item
				for item in res
				if not (
					isinstance(item, (list, tuple))
					and len(item) >= 1
					and isinstance(item[0], str)
					and "modified" in item[0].lower()
				)
			]
	return res


def _merge_filters(configured_filters, runtime_filters):
	if not runtime_filters:
		return configured_filters

	if isinstance(configured_filters, list):
		merged_filters = list(configured_filters)
		for fieldname, value in runtime_filters.items():
			merged_filters.append([fieldname, "=", value])
		return merged_filters

	merged_filters = dict(configured_filters or {})
	merged_filters.update(runtime_filters)
	return merged_filters


def _normalize_list_filters(filters):
	normalized = list(filters or [])
	if normalized and isinstance(normalized[0], str):
		return [normalized]
	return normalized


def _build_xml_node(source, record, mapped_record):
	from lxml import etree

	from openimmo_propms.services.xml_builder import set_xml_value

	immobilie = etree.Element("immobilie")
	for xml_path, value in mapped_record.items():
		set_xml_value(immobilie, xml_path, value)
	_append_image_attachment(source, record, immobilie)
	return immobilie


def _build_record_blocks(source, export_records):
	from lxml import etree

	blocks = []
	for record, mapped_record in export_records:
		blocks.append(etree.tostring(_build_xml_node(source, record, mapped_record), encoding="unicode"))
	return "\n".join(blocks)


def _build_openimmo_template_document(source, export_records, anbieter_id):
	from frappe.utils import now_datetime
	from lxml import etree

	root = etree.fromstring(_OPENIMMO_TEMPLATE_PATH.read_bytes())

	# 1. Fill <uebertragung>
	uebertragung = root.find("uebertragung")
	if uebertragung is not None:
		uebertragung.set("art", "ONLINE")
		uebertragung.set("umfang", source.transfer_scope or "")
		uebertragung.set("modus", source.transfer_mode or "")
		uebertragung.set("version", "1.2.7")
		uebertragung.set("sendersoftware", "OIGEN")
		uebertragung.set("senderversion", "1.0")
		uebertragung.set("timestamp", now_datetime().strftime("%Y-%m-%dT%H:%M:%S"))
		if source.portal_name:
			uebertragung.set("portal", source.portal_name)
		if getattr(source, "regi_id", None):
			uebertragung.set("regi_id", str(source.regi_id))

		# Remove leftover placeholders in attributes
		keys_to_del = [k for k, v in uebertragung.attrib.items() if "{{" in str(v)]
		for k in keys_to_del:
			del uebertragung.attrib[k]

	# 2. Fill <anbieter>
	anbieter = root.find("anbieter")
	if anbieter is not None:
		_set_child_text(anbieter, "anbieternr", anbieter_id or "")
		_set_child_text(anbieter, "firma", getattr(source, "provider_name", "") or "")

		# Clean placeholders in other anbieter children (openimmo_anid, lizenzkennung etc)
		for child in list(anbieter):
			if child.tag != "immobilie":
				if child.text and "{{" in child.text:
					child.text = ""
				keys_to_del = [k for k, v in child.attrib.items() if "{{" in str(v)]
				for k in keys_to_del:
					del child.attrib[k]

	# 3. Handle <immobilie> blocks
	property_template = anbieter.find("immobilie")
	property_index = (
		list(anbieter).index(property_template) if property_template is not None else len(list(anbieter))
	)

	# Remove all template property blocks before inserting real ones
	for child in list(anbieter):
		if child.tag == "immobilie":
			anbieter.remove(child)

	for record, mapped_record in export_records:
		immobilie = _build_openimmo_property_node(source, record, mapped_record, property_template)
		anbieter.insert(property_index, immobilie)
		property_index += 1

	return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode("utf-8")


def _reset_template_node(node, clear_attributes=True):
	# Clear text and tail if they contain placeholders
	if node.text and "{{" in str(node.text):
		node.text = None
	if node.tail and "{{" in str(node.tail):
		node.tail = None

	if clear_attributes:
		node.attrib.clear()
	else:
		# Clear attributes with placeholders
		keys_to_del = [k for k, v in node.attrib.items() if "{{" in str(v)]
		for k in keys_to_del:
			del node.attrib[k]

	for child in list(node):
		_reset_template_node(child, clear_attributes=clear_attributes)


def _set_child_text(parent, tag, value):
	from lxml import etree

	child = parent.find(tag)
	if child is None:
		child = etree.SubElement(parent, tag)
	child.text = "" if value is None else str(value)


def _build_openimmo_property_node(source, record, mapped_record, template_node):
	from lxml import etree

	from openimmo_propms.services.xml_builder import set_xml_value

	immobilie = deepcopy(template_node) if template_node is not None else etree.Element("immobilie")
	_reset_template_node(immobilie)

	for xml_path, value in mapped_record.items():
		set_xml_value(immobilie, xml_path, value)

	_sync_openimmo_action_attribute(immobilie, mapped_record)
	_populate_template_image_attachments(source, record, immobilie)
	return immobilie


def _sync_openimmo_action_attribute(immobilie, mapped_record):
	if any(key.endswith("@aktionart") for key in mapped_record):
		return

	action_value = mapped_record.get("verwaltung_techn.aktion")
	if action_value in (None, ""):
		return

	action_node = immobilie.find("./verwaltung_techn/aktion")
	if action_node is not None:
		action_node.set("aktionart", str(action_value))


def _populate_template_image_attachments(source, record, immobilie):
	from lxml import etree

	image_urls = _collect_image_urls(source, record)
	anhaenge = immobilie.find("anhaenge")

	if anhaenge is None:
		if not image_urls:
			return
		anhaenge = etree.SubElement(immobilie, "anhaenge")

	template_anhang = anhaenge.find("anhang")
	insert_index = 0
	for index, child in enumerate(list(anhaenge)):
		if child.tag == "anhang":
			insert_index = index
			break

	for child in list(anhaenge):
		if child.tag == "anhang":
			anhaenge.remove(child)
		else:
			_reset_template_node(child)

	if not image_urls:
		return

	if template_anhang is None:
		template_anhang = etree.Element("anhang")
		template_anhang.set("location", source.image_location or "EXTERN")
		template_anhang.set("gruppe", source.image_group or "TITELBILD")
		daten = etree.SubElement(template_anhang, "daten")
		etree.SubElement(daten, "pfad")

	for image_url in image_urls:
		anhang = deepcopy(template_anhang)
		_reset_template_node(anhang)

		if source.image_location:
			anhang.set("location", source.image_location)
		if source.image_group:
			anhang.set("gruppe", source.image_group)

		pfad = anhang.find("./daten/pfad")
		if pfad is not None:
			pfad.text = image_url
		else:
			daten = anhang.find("daten")
			if daten is None:
				daten = etree.SubElement(anhang, "daten")
			etree.SubElement(daten, "pfad").text = image_url

		anhaenge.insert(insert_index, anhang)
		insert_index += 1


def _append_image_attachment(source, record, immobilie):
	image_urls = _collect_image_urls(source, record)
	if not image_urls:
		return

	from lxml import etree

	anhaenge = etree.SubElement(immobilie, "anhaenge")
	for image_url in image_urls:
		anhang = etree.SubElement(anhaenge, "anhang")
		etree.SubElement(anhang, "gruppe").text = source.image_group or "TITELBILD"
		etree.SubElement(anhang, "location").text = source.image_location or "EXTERN"
		daten = etree.SubElement(anhang, "daten")
		etree.SubElement(daten, "pfad").text = image_url


def _collect_image_urls(source, record):
	ordered_fields = [
		source.image_field,
		getattr(source, "child_image_field", None),
		getattr(source, "parent_image_field", None),
	]

	image_urls = []
	seen_urls = set()
	for fieldname in ordered_fields:
		image_urls.extend(_resolve_image_urls(source, record, fieldname, seen_urls))

	if image_urls:
		return image_urls

	return _resolve_image_urls(
		source,
		record,
		getattr(source, "fallback_image_field", None),
		seen_urls,
	)


def _resolve_image_urls(source, record, fieldname, seen_urls=None):
	configured_field = (fieldname or "").strip()
	if not configured_field:
		return []

	seen_urls = seen_urls or set()
	image_values = _extract_path_values(record, configured_field, source.target_doctype)
	image_urls = []

	for image_value in image_values:
		image_url = _build_absolute_media_url(source, image_value)
		if not image_url or image_url in seen_urls:
			continue
		image_urls.append(image_url)
		seen_urls.add(image_url)

	return image_urls


def _extract_path_values(record_data, fieldname, root_doctype=None):
	if not isinstance(record_data, dict) or not fieldname:
		return []

	values = _resolve_path_values(record_data, fieldname.split("."), root_doctype)
	return [value for value in values if value not in (None, "")]


def _resolve_path_values(current, parts, current_doctype):
	if not parts:
		return _normalize_path_terminal(current)

	part = parts[0]
	remaining = parts[1:]

	if isinstance(current, dict):
		value = current.get(part)
		if value is None:
			return []

		if isinstance(value, list):
			child_doctype = _get_child_table_options(current_doctype, part)
			resolved = []
			for item in value:
				resolved.extend(_resolve_path_values(item, remaining, child_doctype))
			return resolved

		if isinstance(value, dict):
			return _resolve_path_values(value, remaining, None)

		if remaining:
			linked_doctype = _get_link_options(current_doctype, part)
			if linked_doctype and value:
				linked_doc = frappe.get_doc(linked_doctype, value).as_dict()
				return _resolve_path_values(linked_doc, remaining, linked_doctype)
			return []

		return _normalize_path_terminal(value)

	if isinstance(current, list):
		resolved = []
		for item in current:
			resolved.extend(_resolve_path_values(item, parts, current_doctype))
		return resolved

	return []


def _normalize_path_terminal(value):
	if value in (None, ""):
		return []

	if isinstance(value, list):
		normalized = []
		for item in value:
			normalized.extend(_normalize_path_terminal(item))
		return normalized

	return [value]


def _build_absolute_media_url(source, image_value):
	if not image_value:
		return None

	image_url = str(image_value).strip()
	if not image_url:
		return None

	if image_url.startswith(("http://", "https://")):
		return image_url

	base_url = (source.base_media_url or "").strip() or get_url()
	if not base_url:
		return image_url

	return "{}/{}".format(base_url.rstrip("/"), image_url.lstrip("/"))


def _get_requested_fieldnames(source):
	fieldnames = {"name"}
	meta = frappe.get_meta(source.target_doctype)
	valid_fields = {f.fieldname for f in meta.fields}

	# Always try to fetch property type and unit ID for resolution
	for field in ["custom_property_type", "custom_unit_id"]:
		if field in valid_fields:
			fieldnames.add(field)

	for mapping in source.field_mappings:
		fieldname = _get_configured_fieldname(mapping.target_field)
		if fieldname and "." not in fieldname and fieldname in valid_fields:
			fieldnames.add(fieldname)

	for configured_field in [
		source.name_field,
		source.company_field,
		source.status_field,
		source.publish_field,
		source.image_field,
		getattr(source, "child_image_field", None),
		getattr(source, "parent_image_field", None),
		getattr(source, "fallback_image_field", None),
	]:
		fieldname = _get_configured_fieldname(configured_field)
		if fieldname and "." not in fieldname and fieldname in valid_fields:
			fieldnames.add(fieldname)

	return sorted(fieldnames)


def _sanitize_query_fields(fieldnames):
	cleaned = []
	for fieldname in fieldnames or []:
		text = str(fieldname).strip()
		if text:
			cleaned.append(text)
	return cleaned


def _get_all_records(doctype, filters, fieldnames):
	try:
		return (
			frappe.get_all(
				doctype,
				filters=filters,
				fields=fieldnames,
				limit_page_length=0,
			),
			False,
		)
	except TypeError as exc:
		if "not iterable" not in str(exc):
			raise

		frappe.log_error(
			message=(
				f"TypeError in export get_all for {doctype}. "
				f"fields={fieldnames!r}, filters={filters!r}, error={exc}"
			),
			title="OpenImmo Export Query Fallback",
		)

		return (
			frappe.get_all(
				doctype,
				filters=filters,
				fields=["name"],
				limit_page_length=0,
			),
			True,
		)


def _get_configured_fieldname(value):
	if not isinstance(value, str):
		return ""
	return value.strip()


def _get_required_filter_fieldname(configured_value, label):
	fieldname = _get_configured_fieldname(configured_value)
	if not fieldname:
		frappe.throw(_("{0} is required when corresponding runtime filter is used").format(label))
	return fieldname


def _requires_full_doc(source):
	for configured_field in [
		source.image_field,
		getattr(source, "child_image_field", None),
		getattr(source, "parent_image_field", None),
		getattr(source, "fallback_image_field", None),
	]:
		fieldname = (configured_field or "").strip()
		if fieldname and "." in fieldname:
			return True

	for mapping in source.field_mappings:
		target_field = (mapping.target_field or "").strip()
		if target_field and "." in target_field:
			return True
	return False


def _get_link_options(doctype_name, fieldname):
	if not doctype_name:
		return None

	meta = frappe.get_meta(doctype_name)
	field = meta.get_field(fieldname)
	if field and field.fieldtype == "Link":
		return field.options
	return None


def _get_child_table_options(doctype_name, fieldname):
	if not doctype_name:
		return None

	meta = frappe.get_meta(doctype_name)
	field = meta.get_field(fieldname)
	if field and field.fieldtype == "Table":
		return field.options
	return None


def _should_save_file(source, save_file_flag):
	if save_file_flag is None:
		return cint(source.save_file_by_default)

	return cint(save_file_flag)


def _save_export_file(filename, content, source_name):
	existing_files = frappe.get_all(
		"File",
		filters={
			"file_name": filename,
			"attached_to_doctype": "Integration Source",
			"attached_to_name": source_name,
		},
		pluck="name",
	)
	for fname in existing_files:
		frappe.delete_doc("File", fname, ignore_permissions=True, force=True)

	file_data = save_file_on_filesystem(filename, content, is_private=1)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"file_url": file_data["file_url"],
			"attached_to_doctype": "Integration Source",
			"attached_to_name": source_name,
			"is_private": 1,
		}
	)
	file_doc.flags.copy_from_existing_file = True
	file_doc.flags.ignore_permissions = True
	file_doc.insert()
	return file_doc


def _create_export_job(source, response):
	successful_records = response.get("record_count", 0)
	skipped_records = len(response.get("blocked_records", []))
	total_records = successful_records + skipped_records
	details_by_source = getattr(frappe.local, "openimmo_quality_gate_details", None) or {}
	processing_details = details_by_source.get(source.name, []) if skipped_records else []

	status = "Success"
	if response.get("delivery_status") == "failed":
		status = "Failed"
	elif skipped_records:
		status = "Partially Completed"

	job = frappe.get_doc(
		{
			"doctype": "Integration Job",
			"source_name": source.name,
			"status": status,
			"xml_file": response.get("file_url"),
			"file_name": response.get("filename"),
			"xml_hash": response.get("xml_hash"),
			"delivery_channel": response.get("delivery_channel"),
			"delivery_status": response.get("delivery_status"),
			"delivery_target": response.get("delivery_target"),
			"received_at": frappe.utils.now(),
			"processed_at": frappe.utils.now(),
			"total_records": total_records,
			"successful_records": successful_records,
			"deleted_records": response.get("deleted_records", 0),
			"failed_records": 0,
			"skipped_records": skipped_records,
			"log_message": _build_export_log_message(response),
			"processing_details": processing_details,
		}
	)

	job.insert(ignore_permissions=True)
	return job


def _build_export_log_message(response):
	delivery_status = response.get("delivery_status", "generated")
	delivery_channel = response.get("delivery_channel", "Manual")
	skipped_count = len(response.get("blocked_records", [])) or response.get("skipped_records", 0)
	deleted_count = response.get("deleted_records", 0)
	record_count = response.get("record_count", 0)
	change_count = max(0, record_count - deleted_count)
	return _(
		"Export completed. Records: {0} (CHANGE: {1}, DELETE: {2}), Skipped: {3}, Delivery: {4}, Channel: {5}"
	).format(
		record_count,
		change_count,
		deleted_count,
		skipped_count,
		delivery_status,
		delivery_channel,
	)


def _summarize_export_response(source, responses):
	if source.record_packaging != "Separate XML per Record":
		return responses[0] if responses else {"status": "success", "record_count": 0}

	return {
		"status": "success",
		"record_count": sum(response.get("record_count", 0) for response in responses),
		"skipped_records": sum(response.get("skipped_records", 0) for response in responses),
		"file_count": len(responses),
		"delivery_channel": responses[0].get("delivery_channel") if responses else source.source_type,
		"delivery_status": responses[0].get("delivery_status") if responses else "skipped",
		"files": [
			{
				"filename": response.get("filename"),
				"file_url": response.get("file_url"),
				"job_name": response.get("job_name"),
			}
			for response in responses
		],
	}


def _get_image_bytes_and_name(url):
	import os

	clean_url = (url or "").split("?")[0]
	img_name = clean_url.split("/")[-1] or "image"

	raw_bytes = None
	# 1. Try local filesystem if it's a Frappe file path
	if "/files/" in clean_url or "/private/files/" in clean_url:
		file_part = (
			clean_url[clean_url.find("/files/") :]
			if "/files/" in clean_url
			else clean_url[clean_url.find("/private/files/") :]
		)
		possible_paths = [
			frappe.get_site_path("public", "files", img_name),
			frappe.get_site_path("private", "files", img_name),
			frappe.get_site_path("public", file_part.lstrip("/")),
		]
		for path in possible_paths:
			if os.path.exists(path):
				with open(path, "rb") as f:
					raw_bytes = f.read()
					break

	# 2. Fallback to HTTP download
	if raw_bytes is None and url.startswith(("http://", "https://")):
		resp = requests.get(url, timeout=15, headers={"User-Agent": "OpenImmo-Export/1.0"})
		resp.raise_for_status()
		raw_bytes = resp.content

	if not raw_bytes:
		return None, img_name

	# Convert WebP to JPEG on-the-fly since real estate portals reject .webp
	if img_name.lower().endswith(".webp"):
		try:
			from PIL import Image

			with Image.open(BytesIO(raw_bytes)) as pil_img:
				converted = pil_img.convert("RGB")
				out_buf = BytesIO()
				converted.save(out_buf, format="JPEG", quality=100, subsampling=0)
				jpeg_name = os.path.splitext(img_name)[0] + ".jpg"
				return out_buf.getvalue(), jpeg_name
		except Exception as exc:
			frappe.logger("openimmo_export").warning(f"Failed to convert WebP image {img_name}: {exc}")

	return raw_bytes, img_name


def _build_zip_package(xml_filename, xml_content, image_urls):
	buf = BytesIO()
	with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
		zf.writestr(xml_filename, xml_content.encode("utf-8"))
		for url in image_urls or []:
			try:
				content, img_name = _get_image_bytes_and_name(url)
				if content:
					zf.writestr(img_name, content)
				else:
					frappe.logger("openimmo_export").warning(f"ZIP: could not resolve image content {url}")
			except Exception as e:
				frappe.logger("openimmo_export").warning(f"ZIP: skipped image {url}: {e}")
	buf.seek(0)
	return buf.read()


def _upload_via_ftp(source, filename, content_bytes, xml_hash):
	if not source.ftp_host:
		frappe.throw(_("FTP Host is required for FTP export delivery"))

	delivery_target = _build_ftp_delivery_target(source)
	if _has_successful_ftp_delivery(source.name, xml_hash, delivery_target):
		source.db_set("last_sync_status", _("Duplicate export skipped"))
		return {
			"delivery_channel": "FTP",
			"delivery_status": "skipped_duplicate",
			"delivery_target": delivery_target,
		}

	try:
		ftp = _connect_ftp(source)
		try:
			if source.ftp_directory:
				ftp.cwd(source.ftp_directory)

			ftp.storbinary(f"STOR {filename}", BytesIO(content_bytes))
		finally:
			try:
				ftp.quit()
			except Exception:
				ftp.close()

		source.db_set("last_sync_status", _("Export uploaded successfully"))
		source.db_set("last_sync_at", frappe.utils.now())

		return {
			"delivery_channel": "FTP",
			"delivery_status": "uploaded",
			"delivery_target": delivery_target,
		}
	except Exception as exc:
		error_msg = f"FTP upload failed: {exc!s}"
		frappe.logger("openimmo_export").error(error_msg)
		source.db_set("last_sync_status", _("FTP upload failed: {0}").format(str(exc)[:100]))
		return {
			"delivery_channel": "FTP",
			"delivery_status": "failed",
			"delivery_target": delivery_target,
			"error": str(exc),
		}


def _build_ftp_delivery_target(source):
	if source.ftp_directory:
		return "{}/{}".format(
			source.ftp_host.rstrip("/"),
			source.ftp_directory.strip("/"),
		)
	return source.ftp_host


def _has_successful_ftp_delivery(source_name, xml_hash, delivery_target):
	return bool(
		frappe.db.exists(
			"Integration Job",
			{
				"source_name": source_name,
				"status": ["in", ["Success", "Partially Completed"]],
				"delivery_channel": "FTP",
				"delivery_status": "uploaded",
				"delivery_target": delivery_target,
				"xml_hash": xml_hash,
			},
		)
	)


def _connect_ftp(source):
	host = source.ftp_host
	port = source.ftp_port or 21
	user = source.ftp_username
	password = source.get_password("ftp_password")

	try:
		ftp = ftplib.FTP_TLS(timeout=30)
		ftp.encoding = "utf-8"
		ftp.connect(host, port, timeout=30)
		if user:
			ftp.login(user, password)
			ftp.prot_p()
		else:
			ftp.login()
		ftp.set_pasv(True)
		return ftp
	except Exception as exc:
		frappe.logger("openimmo_export").warning(f"FTP TLS failed, trying plain FTP: {exc!s}")
		ftp = ftplib.FTP(timeout=30)
		ftp.connect(host, port, timeout=30)
		ftp.login(user, password)
		ftp.set_pasv(True)
		return ftp


_validate_quality_gate = validate_quality_gate
