(() => {
  const status = document.getElementById('research-readiness');
  const reports = document.getElementById('research-reports');
  const refresh = document.getElementById('research-refresh');
  async function read(path) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) throw new Error(response.status === 401 ? 'Sign in to view research activity.' : 'Research status unavailable. No readiness claim can be made.');
    return response.json();
  }
  function paragraph(parent, text) {
    const p = document.createElement('p'); p.textContent = text; parent.append(p);
  }
  async function load() {
    refresh.disabled = true;
    try {
      const [readiness, history] = await Promise.all([read('/api/research/readiness'), read('/api/research/reports')]);
      status.textContent = '';
      const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
      summary.textContent = 'Verified configuration and remaining blockers';
      pre.textContent = JSON.stringify(readiness, null, 2); details.append(summary, pre); status.append(details);
      reports.replaceChildren();
      if (!history.reports.length) paragraph(reports, 'No research pass has been recorded. The system is not proven live.');
      for (const report of history.reports) {
        const card = document.createElement('article');
        paragraph(card, `${report.created_at} · ${report.outcome} · ${report.published_proposal_count} review proposals`);
        paragraph(card, `Generation: ${report.generation.status || 'unknown'}. ${report.generation.reason || ''}`);
        for (const text of [...report.blockers, ...report.source_errors, ...report.limitations]) paragraph(card, text);
        for (const lead of report.leads) paragraph(card, `${lead.symbol || ''}: ${lead.claim || 'Source lead'} (retrieved ${lead.retrieved_at || 'unknown'}). ${lead.limitation || ''}`);
        reports.append(card);
      }
    } catch (error) { status.textContent = error.message; reports.replaceChildren(); }
    finally { refresh.disabled = false; }
  }
  refresh.addEventListener('click', load); load();
})();
