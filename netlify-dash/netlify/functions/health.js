const checks = [
  {
    key: 'dashboard',
    label: 'Dashboard Page',
    url: 'https://dash.goldchart.win/',
  },
  {
    key: 'chart',
    label: 'Chart Endpoint',
    url: 'https://goldchart.win/chart_coinbase.html',
  },
  {
    key: 'goldRoot',
    label: 'GoldChart Root',
    url: 'https://goldchart.win/',
  },
];

async function checkUrl(item) {
  const started = Date.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);

  try {
    const response = await fetch(item.url, {
      method: 'GET',
      signal: controller.signal,
      headers: { 'user-agent': 'goldchart-health-check/1.0' },
    });

    return {
      key: item.key,
      label: item.label,
      url: item.url,
      ok: response.ok,
      status: response.status,
      ms: Date.now() - started,
    };
  } catch (error) {
    return {
      key: item.key,
      label: item.label,
      url: item.url,
      ok: false,
      status: 0,
      error: error.name || 'fetch_error',
      ms: Date.now() - started,
    };
  } finally {
    clearTimeout(timeout);
  }
}

exports.handler = async function handler() {
  const results = await Promise.all(checks.map(checkUrl));
  const byKey = Object.fromEntries(results.map((result) => [result.key, result]));
  const chartOk = Boolean(byKey.chart && byKey.chart.ok);

  return {
    statusCode: 200,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    },
    body: JSON.stringify({
      ok: results.every((result) => result.ok),
      tunnel: {
        ok: chartOk,
        inferredFrom: 'https://goldchart.win/chart_coinbase.html',
      },
      checkedAt: new Date().toISOString(),
      results,
    }),
  };
};
