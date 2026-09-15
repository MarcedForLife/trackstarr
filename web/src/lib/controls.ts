// One height for every inline control so they line up: 44px for a thumb, 40
// from sm up. A settings control is 336 wide there, and 36 read as a slot.

export const control = 'h-11 sm:h-10';

// One radius scaled with the height, so the corner cuts the same share of the
// side. Anything nested subtracts its own padding.
export const radius = 'rounded-xl sm:rounded-[11px]';

// The box for every inline text control. 16px on a phone, or iOS Safari zooms
// the page on focus.
export const box = `${control} ${radius} text-base sm:text-xs`;

const filled = `${box} border border-line-strong bg-field disabled:opacity-(--disabled)`;

// The field that takes a row: a path, an address, a key. Mono, since its
// contents are copied rather than written.
export const field = `${filled} w-full px-2.5 font-mono`;

// A cell in a row of them, right-aligned on the digits. The caller says how it
// takes its width, since one of these fills the slack in its row.
export const cell = `${filled} px-2.5 text-right font-mono`;

// A select, in prose with its native arrow.
export const picker = `${filled} w-full px-2`;

// A select in a row of cells: mono like them, read from the left since a name
// is not a number, and tight on the right so the arrow keeps its room.
export const selectCell = `${filled} pr-1 pl-2.5 text-left font-mono`;

// A chip in a row of cells: a code shown rather than typed. The caller pads it
// and says how it takes its width, since one of these fills its row.
export const chip = `${filled} flex items-center font-mono`;

// The empty box at the end of a row where the next entry is typed: dashed, so
// it reads as a place rather than a value.
export const ghost = `${box} border border-dashed border-line-strong bg-transparent px-2.5 font-mono placeholder:text-faint disabled:opacity-(--disabled)`;

// The cross that removes a row: 28 wide by 44 tall. Narrow, so the glyph sits
// one gap off the control beside it rather than adrift in a square, and flush
// right, so a row ends where a rule row's control does.
export const removeButton =
	'-my-2 -ml-2 rounded px-2 py-4 text-faint hover:text-danger disabled:opacity-40';

// A note about the value held, sunken under its row.
export const noteBox = 'rounded-lg border border-line bg-sunken px-3 py-2 text-[12.5px]';

const rowShape =
	'rounded-lg border px-2.5 transition-[translate,box-shadow] duration-150 ease-out sm:px-3.5';

// One whole shadow each rather than a raise stacked on the rest: two arbitrary
// shadows are the same utility, and the stylesheet's order decides which wins.
const rowRest = 'shadow-[0_1px_2px_rgb(0_0_0/0.1),0_3px_8px_rgb(0_0_0/0.08)]';
const rowHeld =
	'relative z-10 -translate-y-0.5 shadow-[0_3px_6px_rgb(0_0_0/0.16),0_14px_28px_rgb(0_0_0/0.22)]';

/** One file lifted off the sunken tray its list sits in, so a row reads as an
 * object rather than a table rule. Picked tints over the fill rather than
 * replacing it, held comes up off the tray with its edge, since a dark shadow
 * on a dark tray says nothing, and armed takes the grid's accent edge. */
export const fileRow = ({ picked = false, held = false, armed = false } = {}) =>
	`${rowShape} ${held ? rowHeld : rowRest} bg-raised ` +
	(armed
		? 'border-accent-fill'
		: picked
			? 'border-accent-fill/55'
			: held
				? 'border-line-strong'
				: 'border-line') +
	(picked ? ' bg-[linear-gradient(var(--accent-soft),var(--accent-soft))]' : '');

// A control at a file row's corner, its target off `after`: tall, and wide
// enough to meet its neighbour's without overlapping it. Hovers to a tint,
// since the tray's colour read as a hole punched in the card. The top pull
// lines every one of them up with the row's first line.
const rowControl =
	`relative -mt-2 inline-flex h-9 w-9 flex-none items-center justify-center rounded-full ` +
	`text-dim transition-colors after:absolute after:-inset-x-0.5 after:-inset-y-1.5 ` +
	`after:content-[''] hover:bg-accent-soft hover:text-fg disabled:opacity-(--disabled)`;

/** The menu that ends the row, pulled the same distance on both axes so it sits
 * square in the corner. */
export const rowMenu = `${rowControl} -mr-1.5 sm:-mr-2.5`;

/** One in from that corner, so it keeps the gap to whatever ends the row. */
export const rowGlyph = rowControl;

/** A mark among them: the same box and target, so it answers the press it
 * looks like it should, without the hover of a control in its own right. */
export const rowMark =
	`relative -mt-2 inline-flex h-9 w-9 flex-none items-center justify-center ` +
	`after:absolute after:-inset-x-0.5 after:-inset-y-1.5 after:content-['']`;

/** A word at that corner rather than a glyph, as Resume is. The same box, so
 * the two line up wherever a list holds both. */
export const rowWord =
	`relative -mt-2 -mr-1.5 inline-flex h-9 flex-none items-center justify-center rounded-full ` +
	`px-2.5 text-[12px] font-medium text-accent transition-colors after:absolute after:-inset-1 ` +
	`after:content-[''] hover:bg-accent-soft disabled:opacity-(--disabled) sm:-mr-2.5`;

// The box every inline button shares, bar its height, which the weights below
// take from `control` unless they say otherwise.
const pill = `${radius} inline-flex items-center justify-center gap-2 px-3.5 text-[13px] whitespace-nowrap transition-colors disabled:opacity-(--disabled)`;
const shape = `${control} ${pill}`;

// Three weights, one shape: plain for a reversible switch, accent for what a
// panel is for, danger for what throws work away. Each hovers a step short of
// where its press lands. The plain one hovers on its edge, since no ground
// token sits between raised and sunken in both themes.
export const button = `${shape} border border-line-strong bg-raised font-medium hover:border-line-control active:bg-sunken`;
// The ink tone, not the fill: on a light page the fill is mid-toned, and a
// near-black label on it reads washed. Dark sets the two the same, so it is
// the amber it always was.
export const primary = `${shape} bg-accent font-semibold text-surface hover:brightness-95 active:brightness-90`;
const dangerTone =
	'border border-danger/45 bg-danger/10 font-semibold text-danger hover:bg-danger/15 active:bg-danger/20';
export const danger = `${shape} ${dangerTone}`;
// The same weight beside a line of text rather than in a row of its own, at the
// height `subtle` uses.
export const dangerInline = `${pill} h-8 ${dangerTone}`;

// A fourth weight, for going somewhere rather than doing something. No box at
// all until it is under the pointer, or a row of these reads as the decision
// the buttons under it are, and shorter than one: the height it saves comes
// back as width wherever a row of them shares a line with something.
export const subtle = `${pill} h-8 font-medium text-dim hover:bg-raised hover:text-fg active:bg-sunken`;

// A button that is only its word: Discard next to Save.
export const quiet = `${shape} font-medium text-dim hover:text-fg`;

// The same word inside another row, as the pair beside a selection count is.
// Tighter than `quiet`, which takes a row of its own.
export const quietInline =
	'rounded-md px-2 py-1 text-[12px] font-medium text-dim transition-colors hover:text-fg disabled:opacity-(--disabled)';

// A button inside a panel rather than on the page: borderless, and 44px tall at
// every width, unlike `control`. Shape only, since the tone has to read on
// whichever ground the panel sits on.
export const rowButton =
	'inline-flex min-h-11 items-center justify-center rounded-lg text-[12px] font-medium disabled:opacity-(--disabled)';

// A button that is only its glyph, as Pause is on a phone. A 44px square with
// 12px corners reads as a knocked-off box, so it goes round; the word and the
// corner come back at sm.
export const glyph = `${control} inline-flex w-11 items-center justify-center gap-2 rounded-full border border-line-strong bg-raised text-[13px] font-medium whitespace-nowrap transition-colors active:bg-sunken disabled:opacity-(--disabled) sm:w-auto sm:rounded-[10px] sm:px-3.5`;

// A button that is only its glyph at every width, as the sweep's pair is: round
// on a phone, squared off beside other controls from sm up. The caller adds the
// fill, since these come in weights like the worded buttons do.
export const iconButton = `${control} inline-flex w-11 flex-none items-center justify-center rounded-full transition-colors disabled:opacity-(--disabled) sm:w-10 sm:rounded-[11px]`;

// What every popover is made of. The tray rather than the card, since most of
// these open over a card and raised over raised cannot read as glass at any
// alpha. The wider blur keeps the words legible at 70.
export const frosted = 'bg-sunken/70 backdrop-blur-xl';

// The veil under anything frosted. Light enough that the glass above it has
// something to show, and one token so the sheet and the drawer cannot drift.
export const scrim = 'bg-black/45';
