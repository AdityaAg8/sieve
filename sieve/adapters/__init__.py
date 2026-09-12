"""Scanner adapters: one interface (`base.ScannerAdapter`) wrapping each native scanner.

Owned entirely by lane L2 (PRD §19). Sieve is a triage layer, not a scanner: nothing in this
package writes detection rules or YARA signatures; adapters only run and parse existing scanners.
"""

from __future__ import annotations
