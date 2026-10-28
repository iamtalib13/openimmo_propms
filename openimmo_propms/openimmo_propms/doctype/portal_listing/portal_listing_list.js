frappe.listview_settings["Portal Listing"] = {
	add_fields: ["state", "last_action"],
	get_indicator: function (doc) {
		if (doc.state === "Online") {
			return [__("Online"), "green", "state,=,Online"];
		} else if (doc.state === "Withdrawn") {
			return [__("Withdrawn"), "red", "state,=,Withdrawn"];
		}
	},
};
