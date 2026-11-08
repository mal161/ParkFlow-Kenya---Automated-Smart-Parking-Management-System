"""
Registration-plate helpers shared by every Django app.

The canonical format ('KAA 123A') must match the Flask engine
(flask_api.data_structures.normalize_registration) and the SQL trigger
in docs/database.sql.
"""


def normalize_registration(reg: str) -> str:
    """Strip whitespace, uppercase, insert a space after the 3rd char."""
    clean = ''.join((reg or '').split()).upper()
    if len(clean) >= 4:
        return clean[:3] + ' ' + clean[3:]
    return clean
