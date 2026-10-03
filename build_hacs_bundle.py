"""Slår ihop www/*.js till dist/smart-energy-manager-cards.js för HACS.

HACS plugin-kategorin letar bara i ROOT_OF_THE_REPO/dist/ eller repots rot,
aldrig i www/ - och en repo-rad motsvarar en enda nedladdningsbar resurs
(hacs.json:s "filename"). Eftersom vi har två kort (laddar-kortet och
energiplan-kortet) paketeras båda i EN fil som registrerar två custom
elements, så HACS-installation bara kräver att användaren lägger till en
Lovelace-resurs för att få båda korten. Kör efter varje ändring i
www/sem-*.js, innan commit - filen i dist/ checkas in, HACS kör ingen
byggsteg själv.
"""
import pathlib

ROOT = pathlib.Path(__file__).parent
SOURCES = ["www/sem-charger-card.js", "www/sem-energy-plan-card.js"]
OUT = ROOT / "dist" / "smart-energy-manager-cards.js"

HEADER = (
    "/**\n"
    " * AUTOGENERERAD av build_hacs_bundle.py - redigera inte direkt.\n"
    " * Källor: {sources}\n"
    " * Regenerera: python build_hacs_bundle.py\n"
    " */\n\n"
)

parts = [HEADER.format(sources=", ".join(SOURCES))]
for src in SOURCES:
    parts.append(f"// ── {src} " + "─" * max(0, 60 - len(src)) + "\n")
    parts.append((ROOT / src).read_text(encoding="utf-8"))
    parts.append("\n")

OUT.parent.mkdir(exist_ok=True)
OUT.write_text("".join(parts), encoding="utf-8", newline="\n")
print(f"Skrev {OUT} ({OUT.stat().st_size} bytes)")
