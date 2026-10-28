// Copyright (c) 2025, Talib sheikh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Integration Job", {
	refresh: function (frm) {
		// 1. Clear existing intro to prevent duplication after Save
		frm.set_intro("");

		// 2. Render Dynamic Header Info
		frm.trigger("render_status_intro");

		// 3. Setup Action Buttons
		frm.trigger("setup_actions");
	},

	render_status_intro: function (frm) {
		if (frm.doc.status === "Pending") {
			if (!frm.is_new() && frm.doc.xml_file) {
				frm.set_intro(
					__("Job is ready for processing. Click 'Process Now' to start sync."),
					"blue"
				);
			} else if (frm.is_new()) {
				frm.set_intro(
					__("Please upload the XML file and save the job to begin processing."),
					"orange"
				);
			} else {
				frm.set_intro(
					__("XML File is missing. Please attach a file and save to proceed."),
					"red"
				);
			}
			return;
		}

		// Modern HTML Cards for Processing Summary
		const total = frm.doc.total_records || 0;
		const success = frm.doc.successful_records || 0;
		const deleted = frm.doc.deleted_records || 0;
		const skipped = frm.doc.skipped_records || 0;
		const failed = frm.doc.failed_records || 0;

		const cards_html = `
			<div style="display: flex; gap: 15px; margin-top: 10px; flex-wrap: wrap;">
				<div style="flex: 1; min-width: 120px; padding: 12px; border-radius: 8px; background: #fff; border: 1px solid #d1d8dd; border-bottom: 4px solid #1a73e8; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
					<div style="font-size: 1.4em; font-weight: bold; color: #1a73e8;">${total}</div>
					<div style="font-size: 0.85em; color: #666; font-weight: 500;">Total Records</div>
				</div>
				<div style="flex: 1; min-width: 120px; padding: 12px; border-radius: 8px; background: #fff; border: 1px solid #d1d8dd; border-bottom: 4px solid #28a745; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
					<div style="font-size: 1.4em; font-weight: bold; color: #28a745;">${success}</div>
					<div style="font-size: 0.85em; color: #666; font-weight: 500;">Success</div>
				</div>
				<div style="flex: 1; min-width: 120px; padding: 12px; border-radius: 8px; background: #fff; border: 1px solid #d1d8dd; border-bottom: 4px solid #6c757d; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
					<div style="font-size: 1.4em; font-weight: bold; color: #6c757d;">${deleted}</div>
					<div style="font-size: 0.85em; color: #666; font-weight: 500;">Deleted</div>
				</div>
				<div style="flex: 1; min-width: 120px; padding: 12px; border-radius: 8px; background: #fff; border: 1px solid #d1d8dd; border-bottom: 4px solid #ffa00a; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
					<div style="font-size: 1.4em; font-weight: bold; color: #ffa00a;">${skipped}</div>
					<div style="font-size: 0.85em; color: #666; font-weight: 500;">Skipped</div>
				</div>
				<div style="flex: 1; min-width: 120px; padding: 12px; border-radius: 8px; background: #fff; border: 1px solid #d1d8dd; border-bottom: 4px solid #dc3545; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
					<div style="font-size: 1.4em; font-weight: bold; color: #dc3545;">${failed}</div>
					<div style="font-size: 0.85em; color: #666; font-weight: 500;">Failed</div>
				</div>
			</div>
		`;

		frm.set_intro(cards_html, "blue");
	},

	setup_actions: function (frm) {
		// Remove existing buttons to prevent duplication on refresh
		frm.clear_custom_buttons();

		// Show button only if the document is saved and not currently processing
		if (!frm.is_new() && frm.doc.status !== "Processing") {
			const btn_label =
				frm.doc.status === "Pending" ? __("Process Now") : __("Re-process Job");

			frm.add_custom_button(btn_label, () => {
				frm.trigger("execute_integration");
			}).addClass("btn-primary");
		}
	},

	execute_integration: function (frm) {
		// Minimalist validation check
		if (!frm.doc.xml_file && !frm.doc.raw_data) {
			frappe.throw(
				__("No data found to process. Please attach a file or provide raw data.")
			);
		}

		frappe.confirm(__("Start processing this integration?"), () => {
			frappe.call({
				// Generic naming convention follow karte hue
				method: "openimmo_propms.services.processor.run_integration_engine",
				args: {
					job_name: frm.doc.name,
				},
				freeze: true,
				freeze_message: __("Syncing Data..."),
				callback: function (r) {
					if (!r.exc) {
						frm.reload_doc();
						frappe.show_alert({
							message: __("Sync Process Completed"),
							indicator: "green",
						});
					}
				},
			});
		});
	},
});
