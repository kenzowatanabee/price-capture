"""Helpers to pull embedded JSON state (VTEX IO / Next.js) out of storefront HTML."""
import json


def find_true_products_array(data):
    """Recursively search the JSON tree to find the TRUE products array for Next.js endpoints."""
    if isinstance(data, dict):
        if 'products' in data:
            candidate = data['products']
            if isinstance(candidate, list) and len(candidate) > 0:
                if isinstance(candidate[0], dict) and 'sku' in candidate[0]:
                    return candidate
        for key, value in data.items():
            result = find_true_products_array(value)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_true_products_array(item)
            if result is not None:
                return result
    return None


def extract_state_from_html(html):
    """Extract embedded JSON state from VTEX or Next.js payloads."""
    template_marker = '<template data-type="json" data-varname="__STATE__">'
    if template_marker in html:
        start_idx = html.find(template_marker) + len(template_marker)
        end_idx = html.find('</template>', start_idx)
        chunk = html[start_idx:end_idx]
        
        script_start = chunk.find('<script>')
        if script_start != -1:
            script_start += len('<script>')
            script_end = chunk.find('</script>', script_start)
            chunk = chunk[script_start:script_end]
            
        try:
            return json.loads(chunk.strip()), "VTEX_IO"
        except Exception:
            pass

    for state_marker in ['window.__STATE__ = ', '__STATE__ = ']:
        if state_marker in html:
            start_idx = html.find(state_marker) + len(state_marker)
            end_idx = html.find('</script>', start_idx)
            if end_idx != -1:
                chunk = html[start_idx:end_idx].strip()
                if chunk.endswith(';'):
                    chunk = chunk[:-1]
                try:
                    return json.loads(chunk), "VTEX_IO"
                except Exception:
                    pass

    next_marker = '<script id="__NEXT_DATA__" type="application/json">'
    if next_marker in html:
        start_idx = html.find(next_marker) + len(next_marker)
        end_idx = html.find('</script>', start_idx)
        if end_idx != -1:
            try:
                return json.loads(html[start_idx:end_idx].strip()), "NEXT_JS"
            except Exception:
                pass

    return None, None
