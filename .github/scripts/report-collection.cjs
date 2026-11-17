// One incident per workflow; never include environment values or provider logs.
module.exports = async ({github, context, core, result}) => {
  const {owner, repo} = context.repo
  const title = `[Court Side] ${context.workflow} needs attention`
  const run = `${context.serverUrl}/${owner}/${repo}/actions/runs/${context.runId}`
  const {data: issues} = await github.rest.issues.listForRepo({owner, repo, state: 'open', creator: 'github-actions[bot]', per_page: 100})
  const existing = issues.find(issue => !issue.pull_request && issue.title === title)
  if (result === 'failure' || result === 'cancelled') {
    const body = `The scheduled **${context.workflow}** job ${result === 'cancelled' ? 'was cancelled' : 'failed'}.\n\n[Inspect the workflow run](${run}). Previous saved data remains available.\n\nThis issue updates on repeat failures and closes after the next successful run.`
    if (existing) {
      await github.rest.issues.update({owner, repo, issue_number: existing.number, body})
    } else {
      await github.rest.issues.create({owner, repo, title, body, assignees: [owner]})
    }
    await core.summary.addHeading('Collection needs attention').addLink('Inspect run', run).write()
  } else if (result === 'success' && existing) {
    await github.rest.issues.createComment({owner, repo, issue_number: existing.number, body: `Recovered: [the latest workflow run passed](${run}).`})
    await github.rest.issues.update({owner, repo, issue_number: existing.number, state: 'closed', state_reason: 'completed'})
  }
}
