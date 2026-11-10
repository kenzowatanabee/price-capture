"""Registry mapping the `type` column of stores.csv to a platform implementation."""
from price_capture.platforms.base import Platform
from price_capture.platforms.rd_nextjs import RdNextJs
from price_capture.platforms.vtex_catalog import VtexCatalog
from price_capture.platforms.vtex_intelligent import VtexIntelligentSearch
from price_capture.platforms.vtex_io_html import VtexIoHtml

PLATFORMS = {
    "B2C_REST": VtexCatalog(),
    "CARREFOUR_INTELLIGENT_SEARCH": VtexIntelligentSearch(),
    "RD_NEXTJS": RdNextJs(),
    "VTEX_IO_HTML": VtexIoHtml(),
}


def get_platform(type_name):
    """The platform for a store type, or None if the type is unknown."""
    return PLATFORMS.get(type_name)
