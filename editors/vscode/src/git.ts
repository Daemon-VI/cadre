// Git for "Review branch": which files a project run changed between its base and
// `cadre/<run-id>`, and each side's content. Pure Node (execFile, never a shell).
//
// Only commit-to-commit commands are used (`diff A...B`, `cat-file blob`), with external diff and
// textconv drivers off, so a repository's own config cannot make these calls run its programs
// through the working tree (fsmonitor) or diff drivers. The extension also only calls this in a
// trusted workspace.
import { execFile } from "node:child_process";

export interface ChangedFile {
  status: string; // A M D T …
  path: string;
}

/** A ref we pass to git as an argument: a sha or a branch name, never something option-like. */
export function isSafeRef(ref: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/.test(ref) && !ref.includes("..") && !ref.endsWith(".lock");
}

/** Parse `git diff --name-status -z` output. With --no-renames every record is `STATUS\0PATH\0`. */
export function parseNameStatusZ(out: string): ChangedFile[] {
  const parts = out.split("\0");
  const files: ChangedFile[] = [];
  for (let i = 0; i + 1 < parts.length; ) {
    const status = parts[i];
    if (!status) break;
    if (/^[RC]/.test(status) && i + 2 < parts.length) {
      // not expected with --no-renames, but read it correctly anyway: STATUS\0OLD\0NEW\0
      files.push({ status: status[0], path: parts[i + 2] });
      i += 3;
    } else {
      files.push({ status: status[0], path: parts[i + 1] });
      i += 2;
    }
  }
  return files;
}

export function git(root: string, args: string[], maxBuffer = 32 * 1024 * 1024): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile("git", ["-C", root, "-c", "core.fsmonitor=false", "--no-pager", ...args],
      { windowsHide: true, maxBuffer, encoding: "utf8" },
      (err, stdout, stderr) => {
        if (err) reject(new Error((stderr || err.message).trim()));
        else resolve(stdout);
      });
  });
}

export async function changedFiles(root: string, base: string, branch: string): Promise<ChangedFile[]> {
  if (!isSafeRef(base) || !isSafeRef(branch)) throw new Error("refusing an unusual ref name");
  const out = await git(root, ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-status", "-z",
    `${base}...${branch}`, "--"]);
  return parseNameStatusZ(out);
}

/** A file's content at `ref`, or undefined when it does not exist there (added / deleted). */
export async function fileAt(root: string, ref: string, relPath: string): Promise<string | undefined> {
  if (!isSafeRef(ref)) throw new Error("refusing an unusual ref name");
  try {
    return await git(root, ["cat-file", "blob", `${ref}:${relPath}`]);
  } catch {
    return undefined;
  }
}
