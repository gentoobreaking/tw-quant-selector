export default function MissingDataSummary({ missing }: { missing: Record<string, number> }) {
  const total = Object.values(missing).reduce((s, n) => s + n, 0);
  if (total === 0) return null;
  return (
    <div style={{
      padding: '8px 16px', fontSize: '12px', color: 'var(--text-muted)',
      borderTop: '1px solid var(--bg-border)', marginTop: '4px',
    }}>
      ⚠ {total} 筆資料不完整
      {Object.entries(missing).filter(([, n]) => n > 0).map(([key, n]) => (
        <span key={key} style={{ marginLeft: '12px' }}>{key}: {n} 筆</span>
      ))}
    </div>
  );
}
