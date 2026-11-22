/**
 * Create the scratch file a temp+fsync+rename write stages into, next to its
 * target, and return its path.
 *
 * The name carries crypto randomness and the open is exclusive. A predictable
 * name (pid + clock alone) is a write-through primitive: anyone able to create
 * files in the draft folder can pre-place a symlink at the path we are about
 * to open and redirect every draft save to a file of their choosing. O_EXCL is
 * the guarantee — it refuses a path that already exists, symlink included —
 * and the randomness keeps a legitimate concurrent writer from colliding with
 * us in the first place. A collision just draws another name; the bytes
 * written are unchanged.
 */
export declare function writeTemp(target: string, content: string): string;
/**
 * Atomically replace (or create) a small state file outside a draft: the user
 * enums catalogue and the app-version tracker. Same temp+fsync+rename as a
 * draft save, plus the parent directory created on demand.
 */
export declare function writeFileAtomic(path: string, content: string): void;
