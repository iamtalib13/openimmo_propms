// Copyright (c) 2026, Talib sheikh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Portal Listing", {
	refresh(frm) {
		const indicator = frm.doc.state === "Online" ? "green" : "red";
		frm.page.set_indicator(frm.doc.state, indicator);
	},
});
