import './Header.css'
export default function Header({ title, subtitle, label, isProcessing, compact = false }: { title: string; subtitle: string; label: string; isProcessing: boolean; compact?: boolean }) {
  return (
    <header className={`header ${compact ? 'header-compact' : ''}`}>
      <div className="header-top">{!compact && <span className="breadcrumb">工作空间 <span>/</span> <strong>{label}</strong></span>}<span className="header-status"><i className={`status-dot ${isProcessing ? 'busy' : ''}`} />{isProcessing ? '正在处理' : '就绪'}</span></div>
      {!compact && <><h2 className="header-title">{title}</h2><p className="header-subtitle">{subtitle}</p></>}
    </header>
  )
}
