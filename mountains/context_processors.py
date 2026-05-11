MAIN_MENU = [
    {'label': 'Prominence tree',    'url_name': 'prominence-tree'},
    {'label': 'Isolation tree',     'url_name': 'isolation-tree'},
    {'label': 'Slope tree',         'url_name': 'slope-tree'},
    {'label': 'Horizon forest',     'url_name': 'horizon-forest'},
    {'label': 'Prominence map',     'url_name': 'map-prominence'},
    {'label': 'Isolation map',      'url_name': 'map-isolation'},
    {'label': 'Statistics',         'url_name': 'statistics'},
    {'label': 'Admin',              'url_name': 'admin:index'},
]

def main_menu(request):
    return {
        'main_menu': MAIN_MENU
    }