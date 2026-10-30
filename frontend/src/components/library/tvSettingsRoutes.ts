/** v327 (Taylor 10:42 spec): Libraries and Options moved to Settings > TV (one page); these are the old ?view= values. */
export const TV_SETTINGS_VIEWS = ['libraries', 'options'] as const;
/** Settings > TV (Library options + Options). /settings/tv stays the TV views (Series | Calendar | Import)
 * that Home > Calendar opens (HomeSearchR3 is a hard stop, so its link is unchanged). */
export const TV_SETTINGS_PATH = '/settings/tv/settings';
