export default function TwseScreener() {
  return (
    <iframe
      src="/twse-screener.html"
      style={{
        width: '100%',
        height: 'calc(100vh - var(--topbar-height, 56px) - 40px)',
        border: 'none',
        borderRadius: 'var(--radius, 10px)',
      }}
      title="台股篩選器"
    />
  );
}
