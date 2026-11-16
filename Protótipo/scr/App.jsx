import { useEffect, useMemo, useState } from 'react'
import {
  Activity, LayoutDashboard, ScanSearch, ClipboardList, Bell,
  CheckCircle2, AlertTriangle, XCircle, Clock3, Package, Camera,
  ChevronRight, Menu, X, TrendingUp, ShieldCheck, CalendarDays,
  Filter, Check, Download, Save, ArrowLeft, FileCheck2
} from 'lucide-react'

const initialSamples = [
  {
    id: '0843',
    imageSrc: '/dataset/Conforme%20(Correto).png',
    product: 'Paracetamol 750mg',
    lotRead: 'L100648',
    expectedLot: 'L100648',
    expiryRead: 'JUL/2027',
    expectedExpiry: 'JUL/2027',
    defect: 'Nenhum defeito identificado',
    reason: 'O lote e a validade estão legíveis e correspondem ao gabarito.',
    aiStatus: 'Conforme',
    aiFinding: 'Nenhuma não conformidade identificada',
    imageExpiry: 'JUL/2027',
    imageLot: 'L100648',
  },
  {
  id: '0844',
  imageSrc: '/dataset/Não%20conforme%20(Correto).png',
  product: 'Paracetamol 750mg',
  lotRead: 'Não identificado',
  expectedLot: 'L100648',
  expiryRead: 'Não identificado',
  expectedExpiry: 'JUL/2027',
  defect: 'Imagem ilegível',
  reason: 'A imagem capturada está desfocada, impedindo a leitura confiável do lote e da validade.',
  aiStatus: 'Não Conforme',
  aiFinding: 'Imagem ilegível — leitura OCR inconclusiva',
  imageExpiry: 'Não identificado',
  imageLot: 'Não identificado',
  },
  {
  id: '0845',
  imageSrc: '/dataset/Conforme%20(Incorreto).png',
  product: 'Paracetamol 750mg',
  lotRead: 'L100648',
  expectedLot: 'L100648',
  expiryRead: 'JUL/2027',
  expectedExpiry: 'JUL/2027',
  defect: 'Nenhum defeito identificado',
  reason: 'O lote e a validade estão legíveis e correspondem ao gabarito.',
  aiStatus: 'Conforme',
  aiFinding: 'Nenhuma não conformidade identificada',
  imageExpiry: 'JUL/2027',
  imageLot: 'L100648',
  },
  {
    id: '0846',
    imageSrc: '/dataset/Não%20conforme%20(Incorreto).png',
    product: 'Paracetamol 750mg',
    lotRead: 'L100649',
    expectedLot: 'L100648',
    expiryRead: 'JUL/2027',
    expectedExpiry: 'JUL/2027',
    defect: 'Divergência de lote identificada',
    reason: 'O lote impresso (L100649) diverge do gabarito (L100648).',
    aiStatus: 'Não Conforme',
    aiFinding: 'Divergência de lote identificada',
    imageExpiry: 'JUL/2027',
    imageLot: 'L100648',
  },
]

const isConforming = value => ['Aprovado', 'Conforme'].includes(value)

// Usa o mesmo computador que entregou o frontend; funciona também pela rede local.
const API_BASE = `${window.location.protocol}//${window.location.hostname}:5000/api`

// O SQLite grava CURRENT_TIMESTAMP em UTC. A conversão abaixo é apenas visual:
 // os horários continuam armazenados em UTC no banco.
function formatDateTime(value) {
  if (!value) return '—'

  const hasTimezone = /(?:Z|[+-]\\d{2}:?\\d{2})$/i.test(value)
  const normalized = hasTimezone ? value : `${value.replace(' ', 'T')}Z`
  const date = new Date(normalized)

  if (Number.isNaN(date.getTime())) return value

  return new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).format(date)
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(result.error || `Erro na API (${response.status})`)
  return result
}

const navItems = [
  { id: 'dashboard', label: 'Visão geral', icon: LayoutDashboard },
  { id: 'inspection', label: 'Inspeção', icon: ScanSearch },
  { id: 'history', label: 'Histórico', icon: ClipboardList },
]

function App() {
  const [page, setPage] = useState('dashboard')
  const [samples, setSamples] = useState(initialSamples)
  const [selectedId, setSelectedId] = useState('0843')
  const [history, setHistory] = useState([])
  const [stats, setStats] = useState({ total: 0, conformes: 0, nao_conformes: 0, pendentes: 0 })
  const [loading, setLoading] = useState(true)
  const [correctedLot, setCorrectedLot] = useState('')
  const [correctedExpiry, setCorrectedExpiry] = useState('')
  const [correctedStatus, setCorrectedStatus] = useState('Conforme')
  const [correctionNote, setCorrectionNote] = useState('')
  const [mobileMenu, setMobileMenu] = useState(false)
  const [toast, setToast] = useState('')
  const [filter, setFilter] = useState('Todos')

  const currentSample = samples.find(item => item.id === selectedId) || samples[0]
  const pendingSamples = samples.filter(item => !item.evaluated)
  const pendingCount = pendingSamples.length
  const approvedCount = stats.conformes
  const rejectedCount = stats.nao_conformes

  const loadData = async () => {
    setLoading(true)
    try {
      const [inspections, feedbacks, nextStats] = await Promise.all([
        apiRequest('/inspections'), apiRequest('/feedbacks'), apiRequest('/stats'),
      ])
      const feedbackByInspection = new Map(feedbacks.map(item => [item.inspection_id, item]))
      const registeredSamples = new Map(inspections.map(item => [String(item.image_path || ''), item]))
      setSamples(initialSamples.map(sample => ({ ...sample, evaluated: registeredSamples.has(sample.id) })))
      setStats(nextStats)
      setHistory(inspections.map(item => {
        const feedback = feedbackByInspection.get(item.id)
        const finalStatus = item.operator_status || item.ai_status
        const lot = feedback?.corrected_lot || item.lot_read || '—'
        const expiry = feedback?.corrected_expiry || item.expiry_read || '—'
        const correction = feedback
          ? `Lote: ${item.lot_read || '—'} → ${lot} | Validade: ${item.expiry_read || '—'} → ${expiry}${feedback.correction_note ? ` | Observação: ${feedback.correction_note}` : ''}`
          : '—'
        return {
          id: `IV-${String(item.id).padStart(6, '0')}`, product: item.product, lot, expiry,
          time: formatDateTime(item.reviewed_at || item.created_at), aiStatus: item.ai_status, finalStatus,
          operatorAction: item.operator_action || 'Aguardando avaliação', correction,
        }
      }))
    } catch (error) {
      notify(`Não foi possível carregar os dados: ${error.message}`)
    } finally { setLoading(false) }
  }

  useEffect(() => { loadData() }, [])

  const notify = (message) => {
    setToast(message)
    window.clearTimeout(window.__ivisToastTimer)
    window.__ivisToastTimer = window.setTimeout(() => setToast(''), 2800)
  }

  const go = (id) => { setPage(id); setMobileMenu(false) }

  const selectSample = (id) => setSelectedId(id)

  const confirmInspection = async () => {
    if (!currentSample || currentSample.evaluated) return
    try {
      await apiRequest('/inspections', {
        method: 'POST',
        body: JSON.stringify({
          product: currentSample.product,
          lot_read: currentSample.lotRead,
          expected_lot: currentSample.expectedLot,
          expiry_read: currentSample.expiryRead,
          expected_expiry: currentSample.expectedExpiry,
          ai_status: currentSample.aiStatus,
          ai_finding: currentSample.aiFinding,
          image_path: currentSample.id,
          operator_status: currentSample.aiStatus,
          operator_action: isConforming(currentSample.aiStatus) ? 'Aprovação confirmada' : 'Reprovação confirmada',
        }),
      })
      await loadData()
      notify(`Resultado confirmado: ${isConforming(currentSample.aiStatus) ? 'CONFORME' : 'NÃO CONFORME'}.`)
    } catch (error) { notify(`Erro ao salvar inspeção: ${error.message}`) }
  }

  const openCorrection = () => {
    setCorrectedLot(currentSample.lotRead === 'Não legível' ? '' : currentSample.lotRead)
    setCorrectedExpiry(currentSample.expiryRead === 'Não legível' ? '' : currentSample.expiryRead)
    setCorrectedStatus(currentSample.aiStatus)
    setCorrectionNote('')
    setPage('correction')
  }

  const saveCorrection = async () => {
    if (!correctedLot.trim() || !correctedExpiry.trim()) {
      notify('Informe o lote e a validade corretos. Se algum dado não puder ser recuperado, registre isso na observação.')
      return
    }
    try {
      await apiRequest('/inspections', {
        method: 'POST',
        body: JSON.stringify({
          product: currentSample.product,
          lot_read: currentSample.lotRead,
          expected_lot: currentSample.expectedLot,
          expiry_read: currentSample.expiryRead,
          expected_expiry: currentSample.expectedExpiry,
          ai_status: currentSample.aiStatus,
          ai_finding: currentSample.aiFinding,
          image_path: currentSample.id,
          corrected_status: correctedStatus,
          corrected_lot: correctedLot.trim(),
          corrected_expiry: correctedExpiry.trim(),
          correction_note: correctionNote.trim(),
        }),
      })
      await loadData()
      setPage('inspection')
      notify(`Correção salva. Resultado final: ${isConforming(correctedStatus) ? 'CONFORME' : 'NÃO CONFORME'}.`)
    } catch (error) { notify(`Erro ao salvar correção: ${error.message}`) }
  }

  const exportCsv = () => {
    const columns = ['Identificação', 'Produto', 'Lote final', 'Validade final', 'Horário', 'Resultado original da IA', 'Resultado final', 'Ação do operador', 'Correção']
    const rows = history.map(item => [item.id, item.product, item.lot, item.expiry, item.time, item.aiStatus, item.finalStatus, item.operatorAction, item.correction])
    const csv = [columns, ...rows].map(row => row.map(value => `"${String(value ?? '').replaceAll('"', '""')}"`).join(';')).join('\n')
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'ivis-historico-inspecoes.csv'
    link.click()
    URL.revokeObjectURL(url)
    notify('Histórico exportado em CSV.')
  }

  const Status = ({ value }) => {
    const styles = {
      Aprovado: 'success',
      Reprovado: 'danger',
      Conforme: 'success',
      'Não Conforme': 'danger',
      'Em avaliação': 'warning',
    }
    const labels = {
      Aprovado: 'Conforme',
      Reprovado: 'Não Conforme',
      Conforme: 'Conforme',
      'Não Conforme': 'Não Conforme',
      'Em avaliação': 'Aguardando avaliação',
    }
    return (
      <span className={`status ${styles[value] || 'neutral'}`}>
        <span />
        {labels[value] || value}
      </span>
    )
  }

  const Header = ({ title, subtitle }) => (
    <header className="page-header">
      <div><div className="eyebrow">IVIS / {title.toUpperCase()}</div><h1>{title}</h1><p>{subtitle}</p></div>
      <div className="header-actions">
        <button className="icon-button notification" onClick={() => notify(pendingCount ? `Você tem ${pendingCount} item(ns) aguardando avaliação.` : 'Nenhuma notificação pendente.')} aria-label="Notificações">
          <Bell size={19} />{pendingCount > 0 && <i />}
        </button>
        <div className="operator"><div className="avatar">OP</div><div><strong>Operador</strong><small>Controle de qualidade</small></div></div>
      </div>
    </header>
  )

  const Dashboard = () => (
    <>
      <Header title="Visão geral" subtitle="Acompanhe o desempenho das inspeções em tempo real." />
      <div className="welcome-banner">
        <div><span className="live"><i /> SISTEMA OPERACIONAL</span><h2>Olá, operador! 👋</h2><p>O sistema está pronto para realizar novas inspeções.</p></div>
        <button className="primary-button" onClick={() => go('inspection')}><ScanSearch size={17} /> Abrir inspeção <ChevronRight size={17} /></button>
        <div className="banner-decoration"><Activity size={115} /></div>
      </div>
      <section className="stats-grid">
        <div className="stat-card"><div className="stat-top"><span>Inspeções registradas</span><div className="stat-icon blue"><Package /></div></div><strong>{stats.total}</strong><small><TrendingUp size={14} /> 12,8% <span>vs. período anterior</span></small></div>
        <div className="stat-card"><div className="stat-top"><span>Conformes</span><div className="stat-icon green"><CheckCircle2 /></div></div><strong>{approvedCount}</strong><small className="green-text">Itens aprovados</small></div>
        <div className="stat-card"><div className="stat-top"><span>Não conformes</span><div className="stat-icon red"><XCircle /></div></div><strong>{rejectedCount}</strong><small className="red-text">Itens reprovados</small></div>
        <div className="stat-card"><div className="stat-top"><span>Aguardando avaliação</span><div className="stat-icon orange"><Clock3 /></div></div><strong>{pendingCount}</strong><small className="orange-text">Pendentes de confirmação</small></div>
      </section>
      <section className="panel recent-panel dashboard-recent">
        <div className="panel-heading"><div><h3>Inspeções recentes</h3><p>Últimos resultados confirmados ou corrigidos</p></div><button className="text-button" onClick={() => go('history')}>Ver histórico <ChevronRight size={15} /></button></div>
        <div className="table-wrap"><table><thead><tr><th>Identificação</th><th>Produto</th><th>Horário</th><th>Resultado final</th><th>Ação do operador</th></tr></thead><tbody>
          {history.slice(0, 8).map(item => <tr key={item.id}><td className="mono">{item.id}</td><td>{item.product}</td><td>{item.time}</td><td><Status value={item.finalStatus} /></td><td>{item.operatorAction}</td></tr>)}
        </tbody></table></div>
      </section>
      <div className="info-strip"><ShieldCheck size={19} /><span><strong>Inspeção assistida:</strong> o operador confirma o resultado quando a IA acerta e corrige somente quando identifica um erro na análise.</span></div>
    </>
  )

  const Inspection = () => (
    <>
      <Header title="Inspeção" subtitle="Acompanhe a análise automática e confirme ou corrija o resultado." />
      <section className="stats-grid inspection-stats">
        <div className="stat-card compact-stat"><span>Itens de demonstração</span><strong>{samples.length}</strong></div>
        <div className="stat-card compact-stat"><span>Conformes na amostra</span><strong className="green-text">{samples.filter(item => isConforming(item.aiStatus)).length}</strong></div>
        <div className="stat-card compact-stat"><span>Não conformes na amostra</span><strong className="red-text">{samples.filter(item => !isConforming(item.aiStatus)).length}</strong></div>
        <div className="stat-card compact-stat"><span>Aguardando avaliação</span><strong>{pendingCount}</strong></div>
      </section>
      <section className="panel sample-selector">
        <div className="panel-heading"><div><h3>Itens aguardando avaliação</h3><p>Selecione um item para conferir o resultado apresentado pela IA.</p></div><span className="status warning"><span /> {pendingCount} pendentes</span></div>
        <div className="sample-buttons">{pendingSamples.map(item => <button key={item.id} className={selectedId === item.id ? 'sample-button active' : 'sample-button'} onClick={() => selectSample(item.id)}><div className="sample-number">#{item.id}</div><div className="sample-info"><strong>{item.product}</strong><small>{item.aiFinding}</small></div><Status value={item.aiStatus} /></button>)}</div>
        {pendingCount === 0 && <div className="empty-state">Todos os itens desta demonstração já foram avaliados.</div>}
      </section>
      {currentSample && !currentSample.evaluated && <>
        <section className="panel reference-panel"><div className="panel-heading"><div><h3><Package size={18} /> Gabarito dinâmico do lote</h3><p>Referência formada automaticamente a partir dos primeiros itens do lote.</p></div><span className="tag">LOTE ATUAL</span></div><div className="reference-values"><div><label>LOTE DE REFERÊNCIA</label><strong className="mono">{currentSample.expectedLot}</strong></div><div><label>VALIDADE DE REFERÊNCIA</label><strong>{currentSample.expectedExpiry}</strong></div><div><label>ORIGEM DO GABARITO</label><strong>Primeiros itens do lote</strong></div></div></section>
        <div className="inspection-layout">
          <section className="panel image-panel"><div className="panel-heading"><div><h3><Camera size={18} /> Imagem da embalagem</h3><p>Visualização da amostra selecionada</p></div><span className="tag">AMOSTRA #{currentSample.id}</span></div>
            <div className="package-stage dataset-stage"><img className="dataset-image" src={currentSample.imageSrc} alt={`Imagem real da amostra ${currentSample.id} do dataset IVIS`} /><div className="scan-corner tl" /><div className="scan-corner tr" /><div className="scan-corner bl" /><div className="scan-corner br" /></div>
          </section>
          <section className="panel result-panel"><div className="panel-heading"><div><h3>Resultado da análise</h3><p>Leitura OCR e validações OCV</p></div><Status value={currentSample.aiStatus} /></div>
            <div className={`result-verdict ${isConforming(currentSample.aiStatus) ? 'approved' : 'rejected'}`}>
              {isConforming(currentSample.aiStatus)
                ? <CheckCircle2 size={34} />
                : <XCircle size={34} />
              }
              <div>
                <span>RESULTADO DA ANÁLISE</span>
                <strong>{isConforming(currentSample.aiStatus) ? 'CONFORME' : 'NÃO CONFORME'}</strong>
                <small>
                  {isConforming(currentSample.aiStatus)
                    ? 'A IA identificou a embalagem como conforme.'
                    : 'A IA identificou uma não conformidade na embalagem.'}
                </small>
              </div>
            </div>
            <div className="data-fields"><div><label>PRODUTO</label><strong>{currentSample.product}</strong></div><div><label>LOTE LIDO (OCR)</label><strong className="mono">{currentSample.lotRead}</strong></div><div><label>LOTE DE REFERÊNCIA</label><strong className="mono">{currentSample.expectedLot}</strong></div><div><label>VALIDADE LIDA (OCR)</label><strong>{currentSample.expiryRead}</strong></div><div><label>VALIDADE DE REFERÊNCIA</label><strong>{currentSample.expectedExpiry}</strong></div></div>
            <div className="analysis-checks"><div><CheckCircle2 /> Etiqueta localizada</div><div className={currentSample.lotRead === currentSample.expectedLot ? 'check-ok' : 'check-error'}>{currentSample.lotRead === currentSample.expectedLot ? <CheckCircle2 /> : <XCircle />}{currentSample.lotRead === currentSample.expectedLot ? 'Lote corresponde ao gabarito' : 'Lote divergente do gabarito'}</div><div className={currentSample.expiryRead === currentSample.expectedExpiry ? 'check-ok' : 'check-error'}>{currentSample.expiryRead === currentSample.expectedExpiry ? <CheckCircle2 /> : <XCircle />}{currentSample.expiryRead === currentSample.expectedExpiry ? 'Validade corresponde ao gabarito' : 'Validade ilegível ou divergente'}</div><div className="check-ok"><CheckCircle2 /> Verificação de formato executada</div></div>
            <div className="operator-feedback"><h4>Avaliação do operador</h4><p>{currentSample.reason} Se a análise estiver correta, confirme. Se a IA tiver errado, solicite a correção.</p>
              <div className="inspection-actions"><button className="primary-button" onClick={confirmInspection}><Check size={17} /> Confirmar resultado</button><button className="secondary-button correction-action" onClick={openCorrection}><AlertTriangle size={16} /> Corrigir resultado</button></div>
              <small className="action-hint">Confirmar mantém o veredito da IA. Corrigir abre uma tela separada para ajustar os dados e o resultado final.</small>
            </div>
          </section>
        </div>
      </>}
    </>
  )

  const Correction = () => (
    <>
      <Header title="Correção pelo operador" subtitle="Ajuste a leitura e o veredito quando identificar um erro na análise automática." />
      <button className="back-link" onClick={() => go('inspection')}><ArrowLeft size={16} /> Voltar para inspeção</button>
      <div className="correction-intro"><div className="correction-intro-icon"><AlertTriangle size={20} /></div><div><strong>Correção da amostra #{currentSample.id}</strong><p>Resultado original da IA: <b>{isConforming(currentSample.aiStatus) ? 'CONFORME' : 'NÃO CONFORME'}</b> — {currentSample.aiFinding}. Altere somente o que estiver incorreto.</p></div></div>
      <div className="inspection-layout correction-layout">
        <section className="panel image-panel"><div className="panel-heading"><div><h3><Camera size={18} /> Imagem da embalagem</h3><p>Use a imagem como referência para a correção.</p></div><span className="tag">AMOSTRA #{currentSample.id}</span></div>
          <div className="package-stage dataset-stage"><img className="dataset-image" src={currentSample.imageSrc} alt={`Imagem real da amostra ${currentSample.id} do dataset IVIS`} /><div className="scan-corner tl" /><div className="scan-corner tr" /><div className="scan-corner bl" /><div className="scan-corner br" /></div>
        </section>
        <section className="panel result-panel"><div className="panel-heading"><div><h3>Dados identificados pela IA</h3><p>Compare a leitura original com a embalagem e o gabarito.</p></div><Status value={currentSample.aiStatus} /></div>
          <div className="data-fields"><div><label>LOTE LIDO PELA IA</label><strong className="mono">{currentSample.lotRead}</strong></div><div><label>LOTE DE REFERÊNCIA</label><strong className="mono">{currentSample.expectedLot}</strong></div><div><label>VALIDADE LIDA PELA IA</label><strong>{currentSample.expiryRead}</strong></div><div><label>VALIDADE DE REFERÊNCIA</label><strong>{currentSample.expectedExpiry}</strong></div></div>
          <div className="correction-form"><h3><FileCheck2 size={17} /> Correção dos dados</h3><p>Informe os valores corretos conforme a embalagem.</p>
            <div className="correction-fields"><label>Lote correto<input value={correctedLot} onChange={e => setCorrectedLot(e.target.value)} placeholder="Digite o lote correto" /></label><label>Validade correta<input value={correctedExpiry} onChange={e => setCorrectedExpiry(e.target.value)} placeholder="MM/AAAA" /></label></div>
            <label className="decision-label status-select-label">Veredito correto da embalagem<select value={correctedStatus} onChange={e => setCorrectedStatus(e.target.value)}><option value="Conforme">CONFORME — embalagem correta</option><option value="Não Conforme">NÃO CONFORME — embalagem com defeito</option></select></label>
            <label className="decision-label note-field">Observação da correção<textarea value={correctionNote} onChange={e => setCorrectionNote(e.target.value)} placeholder="Descreva o erro da IA ou a correção realizada (opcional)." rows={3} /></label>
            <div className="correction-actions"><button className="secondary-button" onClick={() => go('inspection')}>Cancelar</button><button className="primary-button" onClick={saveCorrection}><Save size={16} /> Salvar correção e feedback</button></div>
          </div>
        </section>
      </div>
    </>
  )

  const filteredHistory = useMemo(() => filter === 'Todos' ? history : history.filter(item => filter === 'Aprovado' ? isConforming(item.finalStatus) : !isConforming(item.finalStatus)), [filter, history])
  const History = () => (
    <>
      <Header title="Histórico de inspeções" subtitle="Consulte os resultados originais, confirmações e correções registradas." />
      <section className="panel history-panel"><div className="panel-heading"><div><h3>Registros de inspeção</h3><p>{history.length} registros salvos no banco local</p></div><div className="history-actions"><label className="filter-control"><Filter size={15} /><select value={filter} onChange={e => setFilter(e.target.value)}><option>Todos</option><option value="Aprovado">Conforme</option><option value="Reprovado">Não conforme</option></select></label><button className="secondary-button" onClick={exportCsv}><Download size={16} /> Exportar CSV</button></div></div>
        <div className="table-wrap"><table><thead><tr><th>Identificação</th><th>Produto</th><th>Lote final</th><th>Validade final</th><th>Horário</th><th>Resultado IA</th><th>Resultado final</th><th>Ação do operador</th><th>Correção</th></tr></thead><tbody>
          {filteredHistory.map(item => <tr key={item.id}><td className="mono">{item.id}</td><td>{item.product}</td><td className="mono">{item.lot}</td><td>{item.expiry}</td><td>{item.time}</td><td><Status value={item.aiStatus} /></td><td><Status value={item.finalStatus} /></td><td>{item.operatorAction}</td><td className="correction-cell">{item.correction}</td></tr>)}
        </tbody></table>{filteredHistory.length === 0 && <div className="empty-state">Nenhum registro encontrado para este filtro.</div>}</div>
      </section>
      <div className="info-strip"><CalendarDays size={19} /><span>O histórico diferencia o resultado original da IA do resultado final confirmado ou corrigido pelo operador.</span></div>
    </>
  )

  const content = { dashboard: Dashboard(), inspection: Inspection(), correction: Correction(), history: History() }
  return <div className="app-shell">
    <aside className={`sidebar ${mobileMenu ? 'mobile-open' : ''}`}><div className="brand"><div className="brand-mark"><Activity size={23} /></div><div><strong>IVIS<span>.</span></strong><small>INTELLIGENT INSPECTION</small></div><button className="mobile-close" onClick={() => setMobileMenu(false)}><X /></button></div><div className="side-label">MENU PRINCIPAL</div><nav>{navItems.map(item => { const Icon = item.icon; const active = page === item.id || (page === 'correction' && item.id === 'inspection'); return <button key={item.id} className={active ? 'nav-item active' : 'nav-item'} onClick={() => go(item.id)}><Icon size={19} /><span>{item.label}</span>{active && <i />}</button> })}</nav><div className="sidebar-bottom"><div className="offline"><span /> SISTEMA OPERACIONAL</div><div className="side-version">IVIS <span>v0.3.0</span></div></div></aside>
    {mobileMenu && <div className="mobile-overlay" onClick={() => setMobileMenu(false)} />}
    <main className="main-content">{loading && <div className="info-strip"><Clock3 size={18} /><span>Carregando dados do banco local...</span></div>}<div className="mobile-top"><button onClick={() => setMobileMenu(true)}><Menu /></button><strong>IVIS<span>.</span></strong></div>{content[page]}</main>
    {toast && <div className="toast"><CheckCircle2 size={17} />{toast}</div>}
  </div>
}

export default App
