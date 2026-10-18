/** Short status strings shared by the settings screens. */
export const SAVE_FAILED = "Couldn't save";

/** v175 (Designer): a load error reads "Couldn't load <what>", never the service's own message on top of a prefix. */
export const couldntLoad = (what: string) => `Couldn't load ${what}`;
