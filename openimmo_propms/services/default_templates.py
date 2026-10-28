"""Default Jinja XML templates for OpenImmo export."""

OPENIMMO_JINJA_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!-- OpenImmo $VERSION: 1.2.7 -->
<openimmo>
    <uebertragung art="ONLINE"
                  umfang="{{ source.transfer_scope }}"
                  modus="{{ source.transfer_mode }}"
                  version="1.2.7"
                  sendersoftware="OIGEN"
                  senderversion="1.0"
                  timestamp="{{ frappe.utils.now_datetime().strftime('%Y-%m-%dT%H:%M:%S') }}"
                  {%- if source.regi_id %} regi_id="{{ source.regi_id }}"{% endif -%}/>
    <anbieter>
        {%- set first_doc = all_records[0].doc if all_records else doc -%}
        <anbieternr>{{ source.anbieter_id }}</anbieternr>
        <firma>{{ first_doc.company or "" }}</firma>
        <openimmo_anid>{{ source.openimmo_anid }}</openimmo_anid>

        {# --- Setup Records List for both Single & Batch packaging --- #}
        {%- if all_records -%}
            {%- set records = all_records -%}
        {%- else -%}
            {%- set records = [{'doc': doc}] -%}
        {%- endif -%}

        {%- for rec in records -%}
            {%- set doc = rec.doc -%}
            <immobilie>
                <objektkategorie>
                    {# --- Setup namespace to fetch usage attributes dynamically --- #}
                    {%- set ns_na = namespace(wohnen="false", gewerbe="false", anlage="false", waz="false") -%}
                    {%- if doc.custom_property_type -%}
                        {%- set prop_type = get_document("Property Type", doc.custom_property_type) -%}
                        {%- if prop_type -%}
                            {%- set ns_na.wohnen = "true" if prop_type.use_residential else "false" -%}
                            {%- set ns_na.gewerbe = "true" if prop_type.use_commercial else "false" -%}
                            {%- set ns_na.anlage = "true" if prop_type.use_investment else "false" -%}
                            {%- set ns_na.waz = "true" if prop_type.use_mixed else "false" -%}
                        {%- endif -%}
                    {%- endif -%}
                    <nutzungsart WOHNEN="{{ ns_na.wohnen }}" GEWERBE="{{ ns_na.gewerbe }}" ANLAGE="{{ ns_na.anlage }}" WAZ="{{ ns_na.waz }}"/>
                    <vermarktungsart KAUF="false" MIETE_PACHT="true" ERBPACHT="false" LEASING="false"/>

                    <objektart>
                        {%- if doc.custom_property_type -%}
                            {%- set prop_type = get_document("Property Type", doc.custom_property_type) -%}
                            {%- if prop_type -%}
                                {%- set objektart = prop_type.openimmo_objektart -%}
                                {%- set attribute = prop_type.openimmo_attribute -%}
                                {%- set value = prop_type.openimmo_value -%}
                                {%- if objektart -%}
                                    <{{ objektart }}{% if attribute and value %} {{ attribute }}="{{ value | upper }}"{% endif %}/>
                                {%- else -%}
                                    <sonstige/>
                                {%- endif -%}
                            {%- else -%}
                                <sonstige/>
                            {%- endif -%}
                        {%- else -%}
                            <sonstige/>
                        {%- endif -%}
                        {%- if doc.custom_type_of_property -%}
                        <objektart_zusatz>{{ doc.custom_type_of_property }}</objektart_zusatz>
                        {%- endif -%}
                    </objektart>
                </objektkategorie>

                <geo>
                    {%- if doc.custom_pincode -%}
                    <plz>{{ doc.custom_pincode or "" }}</plz>
                    {%- endif -%}
                    {%- if doc.custom_property_city or (not doc.custom_pincode and not doc.custom_latitude and not doc.custom_longitude) -%}
                    <ort>{{ doc.custom_property_city or "" }}</ort>
                    {%- endif -%}
                    {%- if doc.custom_latitude or doc.custom_longitude -%}
                    <geokoordinaten breitengrad="{{ doc.custom_latitude }}" laengengrad="{{ doc.custom_longitude }}"/>
                    {%- endif -%}
                    {%- if doc.custom_property_street %}<strasse>{{ doc.custom_property_street or "" }}</strasse>{% endif -%}
                    {%- if doc.custom_house_number %}<hausnummer>{{ doc.custom_house_number or "" }}</hausnummer>{% endif -%}
                    <land iso_land="DEU"/>
                    {%- if doc.custom_flurstück %}<flurstueck>{{ doc.custom_flurstück or "" }}</flurstueck>{% endif -%}
                    {%- if doc.custom_level_in_the_building is not none and doc.custom_level_in_the_building != "" -%}
                    <etage>{{ doc.custom_level_in_the_building or "" }}</etage>
                    {%- endif -%}
                    {%- if doc.custom_district %}<regionaler_zusatz>{{ doc.custom_district or "" }}</regionaler_zusatz>{% endif -%}
                    <karten_makro>true</karten_makro>
                </geo>

                <kontaktperson>
                    <email_zentrale>vermietung@axessio.de</email_zentrale>
                    {%- if doc.custom_contact_email %}<email_direkt>{{ doc.custom_contact_email or "" }}</email_direkt>{% endif -%}
                    <tel_zentrale>0000000000</tel_zentrale>
                    {%- if doc.custom_contact_phone %}<tel_durchw>{{ doc.custom_contact_phone or "" }}</tel_durchw>{% endif -%}
                    <tel_handy>0000000000</tel_handy>
                    <name>{{ doc.custom_property_manager or "" }}</name>
                </kontaktperson>

                {%- if doc.custom_building_superintendent -%}
                <weitere_adresse adressart="Hausmeister">
                    <name>{{ doc.custom_building_superintendent or "" }}</name>
                </weitere_adresse>
                {%- endif -%}

                <preise>
                    {%- if doc.custom_new_rent is not none %}<kaltmiete>{{ doc.custom_new_rent }}</kaltmiete>{% endif -%}
                    {%- if doc.custom_new_operating_costs is not none %}<nebenkosten>{{ doc.custom_new_operating_costs }}</nebenkosten>{% endif -%}
                    {%- if doc.custom_additional_costs is not none %}<heizkosten>{{ doc.custom_additional_costs }}</heizkosten>{% endif -%}
                    {%- if doc.custom_plus_19_vat is not none -%}
                    <zzg_mehrwertsteuer>{{ 'true' if doc.custom_plus_19_vat in [True, 'true', 1, '1'] else 'false' }}</zzg_mehrwertsteuer>
                    {%- endif -%}

                    {#- OPTIONAL EXTRA PRICE TAGS Part 1 (Uncomment to use)
                    {%- if doc.custom_flat_rate_rent is not none %}<pauschalmiete>{{ doc.custom_flat_rate_rent }}</pauschalmiete>{% endif -%}
                    {%- if doc.custom_net_operating_costs is not none %}<betriebskostennetto>{{ doc.custom_net_operating_costs }}</betriebskostennetto>{% endif -%}
                    {%- if doc.custom_gross_rent is not none %}<gesamtmietebrutto>{{ doc.custom_gross_rent }}</gesamtmietebrutto>{% endif -%}
                    -#}

                    {%- if doc.custom_commissionfree is not none -%}
                    <provisionspflichtig>{{ 'false' if doc.custom_commissionfree in [True, 'true', 1, '1'] else 'true' }}</provisionspflichtig>
                    {%- endif -%}
                    {%- if doc.custom_commission_description %}<courtage_hinweis>{{ doc.custom_commission_description or "" }}</courtage_hinweis>{% endif -%}
                    <waehrung iso_waehrung="EUR"/>

                    {#- OPTIONAL EXTRA PRICE TAGS Part 2 (Uncomment to use)
                    {%- if doc.custom_development_costs is not none %}<erschliessungskosten>{{ doc.custom_development_costs }}</erschliessungskosten>{% endif -%}
                    -#}

                    {%- if doc.security_deposit is not none %}<kaution>{{ doc.security_deposit }}</kaution>{% endif -%}
                    {%- if doc.custom_deposit is not none %}<kaution_text>{{ doc.custom_deposit or "" }}</kaution_text>{% endif -%}

                    {#- OPTIONAL EXTRA PRICE TAGS Part 3 (Uncomment to use)
                    {%- if doc.custom_carport_rent is not none %}<stp_carport stellplatzmiete="{{ doc.custom_carport_rent }}"/>{% endif -%}
                    {%- if doc.custom_parking_rent is not none %}<stp_freiplatz stellplatzmiete="{{ doc.custom_parking_rent }}"/>{% endif -%}
                    -#}
                </preise>

                <flaechen>
                    {%- if doc.builtup_area is not none and doc.builtup_area | float > 0 %}<wohnflaeche>{{ doc.builtup_area }}</wohnflaeche>{% endif -%}
                    {%- if doc.carpet_area is not none and doc.carpet_area | float > 0 %}<nutzflaeche>{{ doc.carpet_area }}</nutzflaeche>{% endif -%}
                    {%- if doc.custom_property_area is not none and doc.custom_property_area | float > 0 %}<gesamtflaeche>{{ doc.custom_property_area }}</gesamtflaeche>{% endif -%}
                    {%- if doc.bedroom is not none and doc.bedroom | float > 0 %}<anzahl_zimmer>{{ doc.bedroom }}</anzahl_zimmer>{% endif -%}
                    {%- if doc.master_bedroom is not none and doc.master_bedroom | float > 0 %}<anzahl_schlafzimmer>{{ doc.master_bedroom }}</anzahl_schlafzimmer>{% endif -%}
                    {%- if doc.common_bathroom is not none and doc.common_bathroom | float > 0 %}<anzahl_badezimmer>{{ doc.common_bathroom }}</anzahl_badezimmer>{% endif -%}
                    {%- if doc.custom_balcony is not none and doc.custom_balcony | float > 0 %}<anzahl_balkone>{{ doc.custom_balcony }}</anzahl_balkone>{% endif -%}
                    {%- if doc.custom_garage_spaces is not none and doc.custom_garage_spaces | float > 0 %}<anzahl_stellplaetze>{{ doc.custom_garage_spaces }}</anzahl_stellplaetze>{% endif -%}
                </flaechen>

                <ausstattung>
                    {%- if doc.custom_quality_category -%}
                    <ausstatt_kategorie>{{ doc.custom_quality_category }}</ausstatt_kategorie>
                    {%- endif -%}

                    {#- OPTIONAL EXTRA FITTING TAGS Part 1 (Uncomment to use)
                    <bad DUSCHE="{{ 'true' if doc.custom_shower else 'false' }}" WANNE="{{ 'true' if doc.custom_tub else 'false' }}" FENSTER="{{ 'true' if doc.custom_window else 'false' }}"/>
                    <kueche EBK="{{ 'true' if doc.custom_fitted_kitchen else 'false' }}" OFFEN="{{ 'true' if doc.custom_open_kitchen else 'false' }}"/>
                    <boden FLIESEN="{{ 'true' if doc.custom_tiles else 'false' }}" TEPPICH="{{ 'true' if doc.custom_carpet else 'false' }}" PARKETT="{{ 'true' if doc.custom_parquet else 'false' }}"/>
                    <kamin>{{ 'true' if doc.custom_fireplace else 'false' }}</kamin>
                    -#}

                    {%- if doc.custom_type_of_heating -%}
                    <heizungsart OFEN="{{ 'true' if doc.custom_type_of_heating == 'Einzelofen' else 'false' }}"
                                 ZENTRAL="{{ 'true' if doc.custom_type_of_heating == 'Sammelheizung' else 'false' }}"/>
                    {%- endif -%}

                    {%- if doc.custom_energy_carrier or doc.custom_hot_water_preparation -%}
                    <befeuerung GAS="{{ 'true' if doc.custom_energy_carrier == 'Gas' else 'false' }}"
                               SOLAR="{{ 'true' if doc.custom_energy_carrier == 'Solar' else 'false' }}"
                               OEL="{{ 'true' if doc.custom_energy_carrier == 'Öl' else 'false' }}"
                               ELEKTRO="{{ 'true' if doc.custom_energy_carrier == 'Strom' else 'false' }}"
                               KOHLE="{{ 'true' if doc.custom_energy_carrier == 'Kohle' else 'false' }}"
                               FERN="{{ 'true' if doc.custom_energy_carrier == 'Fernwärme' else 'false' }}"
                               WASSER-ELEKTRO="{{ 'true' if doc.custom_hot_water_preparation in ['E-Boiler', 'Elektrodurchlauferhitzer'] else 'false' }}"/>
                    {%- endif -%}

                    {#- OPTIONAL EXTRA FITTING TAGS Part 2 (Uncomment to use)
                    <klimatisiert>{{ 'true' if doc.custom_air_conditioned else 'false' }}</klimatisiert>
                    -#}

                    {%- if doc.custom_elevator is not none -%}
                    <fahrstuhl PERSONEN="{{ 'true' if doc.custom_elevator in [True, 'true', 1, '1'] else 'false' }}"/>
                    {%- endif -%}

                    {%- if doc.custom_garage_spaces is not none -%}
                    <stellplatzart GARAGE="{{ 'true' if doc.custom_garage_spaces else 'false' }}"/>
                    {%- endif -%}

                    {%- if doc.facing -%}
                    <ausricht_balkon_terrasse NORD="{{ 'true' if doc.facing == 'North' else 'false' }}"
                                              NORDOST="{{ 'true' if doc.facing == 'North-East' else 'false' }}"
                                              OST="{{ 'true' if doc.facing == 'East' else 'false' }}"
                                              SUEDOST="{{ 'true' if doc.facing == 'South-East' else 'false' }}"
                                              SUED="{{ 'true' if doc.facing == 'South' else 'false' }}"
                                              SUEDWEST="{{ 'true' if doc.facing == 'South-West' else 'false' }}"
                                              WEST="{{ 'true' if doc.facing == 'West' else 'false' }}"
                                              NORDWEST="{{ 'true' if doc.facing == 'North-West' else 'false' }}"/>
                    {%- endif -%}

                    {%- if doc.furnished -%}
                    <moebliert moeb="{{ 'VOLL' if doc.furnished in [True, 'true', 1, '1'] else 'NICHT_MOEBLIERT' }}"/>
                    {%- endif -%}

                    {#- OPTIONAL EXTRA FITTING TAGS Part 3 (Uncomment to use)
                    <rollstuhlgerecht>{{ 'true' if doc.custom_wheelchair_accessible else 'false' }}</rollstuhlgerecht>
                    <barrierefrei>{{ 'true' if doc.custom_barrier_free else 'false' }}</barrierefrei>
                    <sauna>{{ 'true' if doc.custom_sauna else 'false' }}</sauna>
                    <swimmingpool>{{ 'true' if doc.custom_pool else 'false' }}</swimmingpool>
                    <wintergarten>{{ 'true' if doc.custom_conservatory else 'false' }}</wintergarten>
                    -#}

                    {%- if doc.custom_internet_type -%}
                    <breitband_zugang art="{{ doc.custom_internet_type or "" }}" speed="1000"/>
                    {%- endif -%}

                    {#- OPTIONAL EXTRA FITTING TAGS Part 4 (Uncomment to use)
                    {%- if doc.custom_has_cellar %}<unterkellert keller="{{ doc.custom_has_cellar or "" }}" />{% endif -%}
                    <abstellraum>{{ 'true' if doc.custom_utility_room else 'false' }}</abstellraum>
                    <gaestewc>{{ 'true' if doc.custom_guest_toilet else 'false' }}</gaestewc>
                    <seniorengerecht>{{ 'true' if doc.custom_senior_friendly else 'false' }}</seniorengerecht>
                    -#}
                </ausstattung>

                <zustand_angaben>
                    {%- if doc.custom_year_of_construction -%}
                    <baujahr>{{ doc.custom_year_of_construction or "" }}</baujahr>
                    {%- endif -%}
                    {%- if doc.custom_last_renovation -%}
                    <letztemodernisierung>{{ doc.custom_last_renovation or "" }}</letztemodernisierung>
                    {%- endif -%}
                    {%- if doc.custom_condition -%}
                    <zustand zustand_art="{{ doc.custom_condition or "" }}"/>
                    {%- endif -%}

                    {#- OPTIONAL EXTRA CONDITION TAGS (Uncomment to use)
                    {%- if doc.custom_building_age_category %}<alter alter_attr="{{ doc.custom_building_age_category or "" }}" />{% endif -%}
                    {%- if doc.custom_building_regulations %}<bebaubar_nach bebaubar_attr="{{ doc.custom_building_regulations or "" }}" />{% endif -%}
                    {%- if doc.custom_infrastructure_status %}<erschliessung erschl_attr="{{ doc.custom_infrastructure_status or "" }}" />{% endif -%}
                    -#}

                    {%- if doc.custom_hot_water_preparation -%}
                    <energiepass>
                        <mitwarmwasser>{{ 'true' if doc.custom_hot_water_preparation in ['Gastherme', 'Zentralheizung', 'E-Boiler', 'Elektrodurchlauferhitzer'] else 'false' }}</mitwarmwasser>
                    </energiepass>
                    {%- endif -%}

                    {# --- Energy Certificate: pulls values dynamically from custom Energy Certificate DocType using the property link --- #}
                    {%- set cert = get_document("Energy Certificate", filters={"property": doc.name}) -%}
                    {%- if cert -%}
                    <energiepass>
                        {%- if cert.energy_certificate_type -%}
                        <epart>{{ cert.energy_certificate_type }}</epart>
                        {%- endif -%}
                        {%- if cert.valid_until %}<gueltig_bis>{{ cert.valid_until | format_immowelt_date }}</gueltig_bis>{% endif -%}
                        {%- if cert.energy_value %}<energieverbrauchkennwert>{{ cert.energy_value | format_decimal }}</energieverbrauchkennwert>{% endif -%}
                        <mitwarmwasser>{{ 'true' if cert.with_hot_water in [True, 'true', 1, '1'] else 'false' }}</mitwarmwasser>
                        {%- if cert.final_energy_demand %}<endenergiebedarf>{{ cert.final_energy_demand | format_decimal }}</endenergiebedarf>{% endif -%}
                        {%- if cert.primary_energy_source %}<primaerenergietraeger>{{ cert.primary_energy_source }}</primaerenergietraeger>{% endif -%}
                        {%- if cert.energy_class %}<wertklasse>{{ cert.energy_class or "" }}</wertklasse>{% endif -%}
                        {%- if cert.issue_date %}<ausstelldatum>{{ cert.issue_date or "" }}</ausstelldatum>{% endif -%}
                        {%- if cert.enev_year %}<jahrgang>{{ cert.enev_year or "" }}</jahrgang>{% endif -%}
                    </energiepass>
                    {%- endif -%}

                    {%- if doc.custom_marketing_status -%}
                    <verkaufstatus stand="{{ 'RESERVIERT' if doc.custom_marketing_status in ['RESERVIERT', 'Reserviert', 'true', True, 1, '1'] else 'OFFEN' }}"/>
                    {%- endif -%}
                </zustand_angaben>

                <bewertung>
                    <feld>
                        <name>Anschaffungsdatum</name>
                        <wert>{{ doc.custom_date_of_purchase or "" }}</wert>
                    </feld>
                    <feld>
                        <name>Bodenwert</name>
                        <wert>{{ doc.custom_land_value_per_sqm or "" }}</wert>
                    </feld>
                </bewertung>

                {#- OPTIONAL INFRASTRUCTURE TAGS (Uncomment to use)
                <infrastruktur>
                    <zulieferung>{{ 'true' if doc.custom_delivery_possible else 'false' }}</zulieferung>
                    {%- if doc.custom_view_type %}<ausblick blick="{{ doc.custom_view_type or "" }}" />{% endif -%}
                    {%- if doc.custom_distance_to_school %}<distanzen distanz_zu="HAUPTSCHULE">{{ doc.custom_distance_to_school or "" }}</distanzen>{% endif -%}
                    {%- if doc.custom_distance_to_lake %}<distanzen_sport distanz_zu_sport="SEE">{{ doc.custom_distance_to_lake or "" }}</distanzen_sport>{% endif -%}
                </infrastruktur>
                -#}

                <freitexte>
                    {%- if doc.custom_marketing_title -%}
                    <objekttitel>{{ doc.custom_marketing_title or "" }}</objekttitel>
                    {%- endif -%}
                    {%- if doc.custom_location_short -%}
                    <dreizeiler>{{ doc.custom_location_short or "" }}</dreizeiler>
                    {%- endif -%}
                    {%- if doc.custom_location -%}
                    <lage>{{ doc.custom_location or "" }}</lage>
                    {%- endif -%}
                    {%- if doc.custom_equipment -%}
                    <ausstatt_beschr>{{ doc.custom_equipment or "" }}</ausstatt_beschr>
                    {%- endif -%}
                    {%- if doc.custom_marketing_description -%}
                    <objektbeschreibung>{{ doc.custom_marketing_description or "" }}</objektbeschreibung>
                    {%- endif -%}
                    {%- if doc.custom_additional_information -%}
                    <sonstige_angaben>{{ doc.custom_additional_information or "" }}</sonstige_angaben>
                    {%- endif -%}
                    <objekt_text lang="GER"/>
                </freitexte>

                {# --- Attachment Gallery: dynamically groups hero images as TITELBILD and others as BILD --- #}
                {%- if doc.custom_image_gallery -%}
                <anhaenge>
                    {# --- Check if any image is explicitly flagged as hero_image --- #}
                    {%- set ns_hero = namespace(has_hero=false, hero_written=false) -%}
                    {%- for row in doc.custom_image_gallery -%}
                        {%- if row.is_hero_image -%}
                            {%- set ns_hero.has_hero = true -%}
                        {%- endif -%}
                    {%- endfor -%}

                    {%- for row in doc.custom_image_gallery -%}
                        {%- set img_path = row.get("picture") -%}
                        {%- if img_path -%}
                            {# --- If hero is found, use is_hero_image (guaranteeing only one TITELBILD). Otherwise fallback to first image as hero --- #}
                            {%- set is_hero = false -%}
                            {%- if ns_hero.has_hero -%}
                                {%- if row.is_hero_image and not ns_hero.hero_written -%}
                                    {%- set is_hero = true -%}
                                    {%- set ns_hero.hero_written = true -%}
                                {%- endif -%}
                            {%- else -%}
                                {%- if loop.first -%}
                                    {%- set is_hero = true -%}
                                {%- endif -%}
                            {%- endif -%}

                            <anhang location="EXTERN" gruppe="{{ 'TITELBILD' if is_hero else 'BILD' }}">
                                <format>{{ 'JPEG' if (img_path.split('.')[-1].upper() if '.' in img_path else 'JPEG') == 'WEBP' else (img_path.split('.')[-1].upper() if '.' in img_path else 'JPEG') }}</format>
                                <daten>
                                    <pfad>{{ img_path if img_path.startswith(('http://', 'https://')) else ((source.base_media_url or frappe.utils.get_url()).rstrip('/') ~ '/' ~ img_path.lstrip('/')) }}</pfad>
                                </daten>
                            </anhang>
                        {%- endif -%}
                    {%- endfor -%}
                </anhaenge>
                {%- endif -%}

                <verwaltung_objekt>
                    {%- if doc.custom_available_from -%}
                    <verfuegbar_ab>{{ doc.custom_available_from or "" }}</verfuegbar_ab>
                    {%- endif -%}
                    {%- if doc.status -%}
                    <vermietet>{{ 'true' if doc.status == 'Rented' else 'false' }}</vermietet>
                    {%- endif -%}
                    {%- if doc.custom_pets_allowed is not none -%}
                    <haustiere>{{ 'true' if doc.custom_pets_allowed in [True, 'true', 1, '1'] else 'false' }}</haustiere>
                    {%- endif -%}
                    {%- if doc.custom_monument_protection is not none -%}
                    <denkmalgeschuetzt>{{ 'true' if doc.custom_monument_protection in [True, 'true', 1, '1'] else 'false' }}</denkmalgeschuetzt>
                    {%- endif -%}
                </verwaltung_objekt>

                <verwaltung_techn>
                    <objektnr_intern>{{ (doc.name[2:] if (doc.name or "").startswith("E-") else (doc.name or "")) }}</objektnr_intern>
                    <objektnr_extern>{{ (doc.name[2:] if (doc.name or "").startswith("E-") else (doc.name or "")) }}</objektnr_extern>
                    <aktion aktionart="{{ rec.action or source.transfer_mode or 'CHANGE' }}"/>
                    {%- if doc.custom_available_from -%}
                    <aktiv_von>{{ doc.custom_available_from or "" }}</aktiv_von>
                    {%- endif -%}
                    <openimmo_obid>{{ (doc.name[2:] if (doc.name or "").startswith("E-") else (doc.name or "")) }}</openimmo_obid>
                    <stand_vom>{{ doc.modified.strftime('%Y-%m-%d') if doc.modified else frappe.utils.nowdate() }}</stand_vom>
                </verwaltung_techn>
            </immobilie>
        {%- endfor -%}
    </anbieter>
</openimmo>
"""
