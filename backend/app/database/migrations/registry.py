"""Append migrations in increasing order; published entries are immutable."""
from .v001_coordination import upgrade as v001_coordination
from .v002_workshop import upgrade as v002_workshop
from .v003_metadata import upgrade as v003_metadata
from .v004_llm_usage import upgrade as v004_llm_usage
from .v005_leadership_snapshots import upgrade as v005_leadership_snapshots
from .v006_report_snapshots import upgrade as v006_report_snapshots
from .v007_automation import upgrade as v007_automation
from .v008_definitions import upgrade as v008_definitions
from .v009_downloads import upgrade as v009_downloads
from .v010_dispatch import upgrade as v010_dispatch
from .v011_energy import upgrade as v011_energy
from .v012_alarms import upgrade as v012_alarms
from .v013_retention import upgrade as v013_retention

MIGRATIONS = (
    ("001_coordination", v001_coordination),
    ("002_workshop", v002_workshop),
    ("003_metadata", v003_metadata),
    ("004_llm_usage", v004_llm_usage),
    ("005_leadership_snapshots", v005_leadership_snapshots),
    ("006_report_snapshots", v006_report_snapshots),
    ("007_automation", v007_automation),
    ("008_definitions", v008_definitions),
    ("009_downloads", v009_downloads),
    ("010_dispatch", v010_dispatch),
    ("011_energy", v011_energy),
    ("012_alarms", v012_alarms),
    ("013_retention", v013_retention),
)
