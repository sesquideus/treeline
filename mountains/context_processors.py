MAIN_MENU = [
    {'label': 'Prominence forest',  'url_name': 'prominence-forest'},
    {'label': 'Isolation forest',   'url_name': 'isolation-forest'},
    {'label': 'Prominence map',     'url_name': 'map-prominence'},
    {'label': 'Isolation map',      'url_name': 'map-isolation'},
    {'label': 'Admin',              'url_name': 'admin:index'},
#    {'label': 'About',              'url_name': 'about'},
]

def main_menu(request):
    return {
        'main_menu': MAIN_MENU
    }