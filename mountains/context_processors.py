MAIN_MENU = [
    {'label': 'Prominence tree',    'url_name': 'prominence-tree'},
    {'label': 'Isolation tree',     'url_name': 'isolation-tree'},
    {'label': 'Slope tree',         'url_name': 'slope-tree'},
    {'label': 'Horizon forest',     'url_name': 'horizon-forest'},

    {'label': 'Map',                'url_name': 'map'},
    {'label': 'Statistics',         'url_name': 'statistics'},

    {'label': 'List of mountains',  'url_name': 'mountain-list'},
    {'label': 'List of cols',       'url_name': 'col-list'},

    {'label': 'Compare',            'cls': 'compare', 'url_name': 'summit-compare'},
    {'label': 'Admin',              'cls': 'admin', 'url_name': 'admin:index'},
]

def main_menu(request):
    return {
        'main_menu': MAIN_MENU
    }