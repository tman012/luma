"""Settings premium pass: the one list of room icons (also mirrored in frontend roomIcons.tsx).
No app imports, so app.settings_manager and app.rooms_setup can both import it without a cycle."""
ROOM_ICONS = ('sofa', 'bed', 'kitchen', 'dining', 'office', 'theater', 'game', 'kids', 'bath', 'gym',
              'music', 'library', 'patio', 'garage', 'home')
DEFAULT_ICON = 'home'
