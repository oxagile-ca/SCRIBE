/**
 * Queue-row action semantics.
 *
 * A ticket that has already been QA'd looked identical to one that never has:
 * both offered a plain "Start" that ran the FULL pipeline (provision → build →
 * deploy → test) behind the env picker, when a re-test normally only needs the
 * QA stage. The lane card already relabels itself to "Retry QA"; this gives the
 * queue the same affordance.
 */

/** Button label for a queue row: QAed work is re-tested, not started. */
export function queueActionLabel(isQAed: boolean): string {
  return isQAed ? 'Re-test' : 'Start'
}

/**
 * Does a re-test have to ask which environment to run against?
 *
 * Already-deployed apps: no. The backend resolves an empty envUrl via
 * qa_orchestrator.resolve_env_url -> environments.staticUrls[0], so there is
 * exactly one answer and asking is a wasted click.
 *
 * Build/deploy apps: yes. staticUrls may be empty, in which case resolve_env_url
 * returns "" and the run would silently test nothing — so make the user choose.
 */
export function retestNeedsEnvPicker(needsBuildDeploy: boolean): boolean {
  return needsBuildDeploy
}
