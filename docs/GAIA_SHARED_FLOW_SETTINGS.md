# Gemeinsame GAIA-Flow-Einstellungen

Wenn Water Flow auf einem aktivierten Cloth-Objekt eingeschaltet wird, nehmen alle aktivierten Cloth-Objekte derselben Szene teil. Ausschalten auf einem Objekt schaltet Flow gemeinsam aus. Domain, Coupling Strength, Velocity Damping, Field Resolution, `.gaia`-Pfad und Flow-Vorschau werden gemeinsam geändert.

**Influence und Velocity Scale bleiben pro Objekt gespeichert.** Unterschiedliche Werte werden weder beim Teilen noch beim Laden überschrieben. Die bestehende Einschränkung von Native GAIA 0.26 für Velocity Scale bleibt bestehen; das Teilen ändert keine Solver-Physik.

Neu aktivierte, duplizierte oder hinzugefügte Cloth-Objekte übernehmen die gemeinsame Konfiguration. Collider und andere Rollen werden nicht einbezogen. Separate Szenen mit eigenen Objekten bleiben unabhängig. Der automatische Cache-Pfad wird beim ersten Prepare zu einem gemeinsamen `.gaia`-Dateipfad aufgelöst.

Die Szene merkt sich das zuletzt bearbeitete gemeinsame Setup über dessen Quellobjekt. Bestehende Dateien mit aktiviertem Flow übernehmen beim Laden das Setup eines aktivierten Flow-Cloth-Objekts; das aktive Objekt der ersten View Layer wird bevorzugt, ansonsten die alphabetisch erste Quelle. Die individuellen Influence-/Velocity-Scale-Werte bleiben erhalten. Löschen oder Deaktivieren der Quelle unterbricht das Teilen der verbleibenden Cloth-Objekte nicht.

Prüfwerkzeuge: `tests/test_shared_water_settings.py` und `tools/blender_shared_water_flow_smoke.py`. Der Blender-Test prüft echte RNA-Änderungen, neue Cloth-Objekte, getrennte Szenen, Speichern/Laden, Altdatei-Übernahme, gemeinsame automatische Cache-Pfade und Handler-Aufräumen.
