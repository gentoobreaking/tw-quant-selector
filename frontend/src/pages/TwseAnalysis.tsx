export default function TwseAnalysis() {
  return (
    <iframe
      src="/twse-analysis.html"
      style={{
        width: '100%',
        height: 'calc(100vh - var(--topbar-height, 56px) - 40px)',
        border: 'none',
        borderRadius: 'var(--radius, 10px)',
      }}
      title="TWSE 資料分析"
    />
  );
}
