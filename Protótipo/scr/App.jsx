import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, LayoutDashboard, ScanSearch, ClipboardList, Bell,
  CheckCircle2, AlertTriangle, XCircle, Clock3, Package, Camera,
  ChevronRight, Menu, X, TrendingUp, ShieldCheck, CalendarDays,
  Filter, Check, Download, Save, ArrowLeft, FileCheck2
} from 'lucide-react'

const isConforming = value => ['Aprovado', 'Conforme'].includes(value)

const MONTHS = ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN', 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ']

function normalizeLot(value) {
  // O backend grava 'Não identificado' quando o OCR não lê o lote.
  if (String(value ?? '').trim() === 'Não identificado') return 'Não identificado'
  return String(value ?? '')
    .trim()
    .toUpperCase()
    .replace(/\s+/g, '')
    .replace(/^L(?=\d+$)/, '')
}

// Padroniza datas para comparação, aceitando JUL/25 e JUL/2025.
function normalizeMonthYear(value) {
  const text = String(value ?? '').trim().toUpperCase().replace(/\s+/g, '')
  const match = text.match(/^([A-Z]{3})\/(\d{2}|\d{4})$/)

  if (!match) return text

  const year = match[2].length === 2 ? `20${match[2]}` : match[2]
  return `${match[1]}/${year}`
}

// Máscara de entrada: MMM/AA.
function formatMonthYearInput(value) {
  const text = String(value ?? '')
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '')

  const month = text.slice(0, 3).replace(/[^A-Z]/g, '')
  const year = text.slice(3, 5).replace(/\D/g, '')

  if (month.length < 3) return month
  return year ? `${month}/${year}` : `${month}/`
}

function formatMonthYearDisplay(value) {
  const normalized = normalizeMonthYear(value)
  const match = normalized.match(/^([A-Z]{3})\/(20\d{2})$/)

  return match ? `${match[1]}/${match[2].slice(-2)}` : (value || 'Não identificado')
}

function formatOcrValue(value) {
  const text = String(value ?? '').trim()
  const normalized = text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toUpperCase()
    .replace(/\s/g, '')

  if (!text || normalized === 'NAOIDENTIFICADO') {
    return 'Não identificado'
  }

  return text
}

// Usa o mesmo computador que entregou o frontend; funciona também pela rede local.
const API_BASE = `${window.location.protocol}//${window.location.hostname}:5000/api`

async function analyzeVideo(file, reprocess = false) {
  const formData = new FormData()
  formData.append('video', file)
  if (reprocess) formData.append('reprocessar', '1')

  const response = await fetch(`${API_BASE}/detect-video`, {
    method: 'POST',
    body: formData,
  })

  const result = await response.json().catch(() => ({}))

  if (!response.ok) {
    const error = new Error(result.error || `Erro na análise (${response.status})`)
    error.duplicate = Boolean(result.duplicate)
    throw error
  }

  return result
}

function backendImageUrl(path) {
  if (!path) return ''
  if (path.startsWith('http')) return path

  return `${window.location.protocol}//${window.location.hostname}:5000${path}`
}

// O SQLite grava CURRENT_TIMESTAMP em UTC. A conversão abaixo é apenas visual.
function formatDateTime(value) {
  if (!value) return '—'

  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
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

const GABARITO_FIELDS = [
  { key: 'lote', label: 'Lote' },
  { key: 'fabricacao', label: 'Fabricação' },
  { key: 'validade', label: 'Validade' },
]

// Painel do gabarito de UM processo (vídeo): as 3 embalagens de referência,
// o gabarito definido e a confirmação/correção pelo operador.
// Fica fora de App para manter o estado do formulário entre renderizações.
function GabaritoPanel({ info, onSaved, notify }) {
  const gabarito = info?.gabarito
  const pending = Boolean(gabarito?.pendente)
  const divergentes = new Set((gabarito?.divergencias || []).map(d => d.campo))
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  const [values, setValues] = useState(() => ({
    lote: gabarito?.lote || '',
    fabricacao: gabarito?.fabricacao || '',
    validade: gabarito?.validade || '',
  }))

  if (!gabarito) return null
  const showForm = pending || editing

  const save = async () => {
    if (GABARITO_FIELDS.some(f => !String(values[f.key]).trim())) {
      notify('Informe lote, fabricação e validade do gabarito.')
      return
    }
    setSaving(true)
    try {
      const result = await apiRequest(`/processes/${info.process.id}/gabarito`, {
        method: 'POST',
        body: JSON.stringify({ ...values, observacao: note.trim() }),
      })
      setEditing(false)
      setNote('')
      notify(`${result.message}. ${result.inspecoes_reavaliadas} inspeção(ões) reavaliada(s).`)
      await onSaved()
    } catch (error) {
      notify(`Erro ao salvar gabarito: ${error.message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="panel reference-panel gabarito-panel">
      <div className="panel-heading">
        <div>
          <h3><Package size={18} /> Gabarito do processo #{info.process.id}</h3>
          <p>
            Definido pelas 3 primeiras embalagens válidas do vídeo
            {' '}<span className="mono">{info.process.video_name}</span>.
          </p>
        </div>
        <span className={`status ${pending ? 'warning' : 'success'}`}>
          <span /> {gabarito.status}
        </span>
      </div>

      <div className="reference-values">
        {GABARITO_FIELDS.map(f => (
          <div key={f.key}>
            <label>{f.label.toUpperCase()} DE REFERÊNCIA</label>
            <strong className={gabarito[f.key] ? 'mono' : 'orange-text'}>
              {gabarito[f.key] || 'Pendente'}
            </strong>
          </div>
        ))}
      </div>

      <p className="gabarito-note">
        <b>Origem:</b> {gabarito.origem}. {gabarito.motivo}
        {gabarito.confirmed_at && <> Confirmado em {formatDateTime(gabarito.confirmed_at)}.</>}
      </p>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Referência</th>
              {GABARITO_FIELDS.map(f => <th key={f.key}>{f.label} lido</th>)}
            </tr>
          </thead>
          <tbody>
            {gabarito.leituras_referencia.length === 0 && (
              <tr><td colSpan={4}>Nenhuma embalagem com leitura completa.</td></tr>
            )}
            {gabarito.leituras_referencia.map(ref => (
              <tr key={ref.sequence}>
                <td className="mono">Embalagem {ref.sequence}{ref.inspection_id ? ` · #${ref.inspection_id}` : ''}</td>
                {GABARITO_FIELDS.map(f => (
                  <td key={f.key} className={`mono ${divergentes.has(f.key) ? 'red-text' : ''}`}>
                    {ref[f.key]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pending && (
        <div className="video-upload-error gabarito-alert">
          <AlertTriangle size={15} /> As embalagens deste processo só serão classificadas
          depois que o operador confirmar o gabarito. Confira a embalagem física: o sistema
          não escolhe valores divergentes sozinho.
        </div>
      )}

      {showForm ? (
        <div className="correction-form">
          <h3><FileCheck2 size={17} /> {pending ? 'Confirmar gabarito' : 'Revisar gabarito'}</h3>
          <div className="correction-fields gabarito-fields">
            {GABARITO_FIELDS.map(f => {
              const lidos = [...new Set(gabarito.leituras_referencia.map(r => r[f.key]).filter(Boolean))]
              return (
                <label key={f.key}>
                  {f.label} {divergentes.has(f.key) && <span className="red-text">(divergente)</span>}
                  <input
                    value={values[f.key]}
                    onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value.toUpperCase() }))}
                    placeholder={f.key === 'lote' ? 'Lote correto' : 'MMM/AA'}
                  />
                  {divergentes.has(f.key) && (
                    <span className="gabarito-options">
                      Valores lidos:{' '}
                      {lidos.map(v => (
                        <button key={v} type="button" className="tag"
                          onClick={() => setValues(prev => ({ ...prev, [f.key]: v }))}>
                          {v}
                        </button>
                      ))}
                    </span>
                  )}
                </label>
              )
            })}
            <label>
              Observação
              <input value={note} onChange={e => setNote(e.target.value)}
                placeholder="Ex.: conferido na embalagem física" />
            </label>
          </div>
          <div className="correction-actions">
            {!pending && (
              <button className="secondary-button" onClick={() => setEditing(false)}>Cancelar</button>
            )}
            <button className="primary-button" onClick={save} disabled={saving}>
              <Save size={16} /> {saving ? 'Salvando...' : 'Salvar gabarito'}
            </button>
          </div>
        </div>
      ) : (
        <button className="secondary-button" onClick={() => setEditing(true)}>
          <FileCheck2 size={15} /> Revisar gabarito
        </button>
      )}
    </section>
  )
}

const navItems = [
  { id: 'dashboard', label: 'Visão geral', icon: LayoutDashboard },
  { id: 'inspection', label: 'Inspeção', icon: ScanSearch },
  { id: 'history', label: 'Histórico', icon: ClipboardList },
]

function App() {
  const [page, setPage] = useState('dashboard')
  const [samples, setSamples] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [history, setHistory] = useState([])
  const [stats, setStats] = useState({ total: 0, conformes: 0, nao_conformes: 0, pendentes: 0 })
  const [loading, setLoading] = useState(true)
  const [correctedLot, setCorrectedLot] = useState('')
  const [correctedExpiry, setCorrectedExpiry] = useState('')
  const [correctedManufacturing, setCorrectedManufacturing] = useState('')
  const [correctedStatus, setCorrectedStatus] = useState('Conforme')
  const [correctionNote, setCorrectionNote] = useState('')
  const [mobileMenu, setMobileMenu] = useState(false)
  const [toast, setToast] = useState('')
  const [filter, setFilter] = useState('Todos')

  const [analyzing, setAnalyzing] = useState(false)
  const [savingInspection, setSavingInspection] = useState(false)
  const [liveSample, setLiveSample] = useState(null)
  const [analysisError, setAnalysisError] = useState('')
  const importImagesPromise = useRef(null)

  const currentSample =
    liveSample?.id === selectedId
      ? liveSample
      : samples.find(item => item.id === selectedId) || samples[0]

  // Gabarito do processo (vídeo) da inspeção selecionada.
  const [processInfo, setProcessInfo] = useState(null)
  const currentProcessId = currentSample?.processId || null

  const loadProcessInfo = async (processId = currentProcessId) => {
    if (!processId) {
      setProcessInfo(null)
      return
    }
    try {
      setProcessInfo(await apiRequest(`/processes/${processId}`))
    } catch (error) {
      setProcessInfo(null)
      notify(`Não foi possível carregar o gabarito: ${error.message}`)
    }
  }

  useEffect(() => {
    loadProcessInfo(currentProcessId)
  }, [currentProcessId])

  const awaitingGabarito = currentSample?.aiStatus === 'Aguardando gabarito'

  const pendingSamples = samples.filter(item => item.databaseId && !item.evaluated)
  const pendingCount = pendingSamples.length
  const approvedCount = stats.conformes
  const rejectedCount = stats.nao_conformes

  const handleVideoUpload = async (event) => {
  const file = event.target.files?.[0]
  if (!file) return

  if (!file.type.startsWith('video/')) {
    notify('Selecione um arquivo de vídeo válido.')
    return
  }

  setAnalyzing(true)
  setAnalysisError('')
  setLiveSample(null)

  try {
    let result
    try {
      result = await analyzeVideo(file)
    } catch (error) {
      // Vídeo já processado: só reprocessa se o operador confirmar.
      if (!error.duplicate || !window.confirm(`${error.message}\n\nDeseja reprocessar mesmo assim?`)) {
        throw error
      }
      result = await analyzeVideo(file, true)
    }

    if (!Array.isArray(result.inspections) || result.inspections.length === 0) {
      throw new Error('Nenhuma embalagem foi identificada no vídeo.')
    }

    await loadData()

    setPage('inspection')
    setLiveSample(null)

    const gabaritoMsg = result.gabarito?.pendente
      ? ' Gabarito pendente de confirmação.'
      : result.gabarito ? ' Gabarito definido automaticamente.' : ''
    notify(`${result.inspections.length} inspeção(ões) gerada(s) pelo vídeo.${gabaritoMsg}`)
  } catch (error) {
    setAnalysisError(error.message)
    notify(`Erro ao analisar vídeo: ${error.message}`)
  } finally {
    setAnalyzing(false)
    event.target.value = ''
  }
}

  const loadData = async () => {
    setLoading(true)
    try {

      const [inspections, feedbacks, nextStats] = await Promise.all([
        apiRequest('/inspections'),
        apiRequest('/feedbacks'),
        apiRequest('/stats'),
      ])

      const feedbackByInspection = new Map(
        feedbacks.map(item => [item.inspection_id, item])
      )

      const importedSamples = inspections.map(item => {
        let errors = []
        try {
          errors = item.ocr_errors ? JSON.parse(item.ocr_errors) : []
        } catch {
          errors = []
        }

        const originalName = String(item.image_path || '').replace(/^test_images[\\/]/, '')
        const originalUrl = originalName
          ? backendImageUrl(`/api/test-images/${encodeURIComponent(originalName)}`)
          : ''

        const annotatedUrl = item.annotated_image
          ? backendImageUrl(`/api/processed/${encodeURIComponent(item.annotated_image)}`)
          : originalUrl

        const cropUrl = item.crop_image
          ? backendImageUrl(`/api/processed/${encodeURIComponent(item.crop_image)}`)
          : ''

        return {
          id: String(item.id),
          databaseId: item.id,
          lotRead: normalizeLot(item.lot_read) || 'Não identificado',
          expectedLot: normalizeLot(item.expected_lot || '100648'),
          manufacturingRead: formatMonthYearDisplay(item.manufacturing_read),
          expiryRead: formatMonthYearDisplay(item.expiry_read),
          expectedExpiry: item.expected_expiry || 'JUL/2027',
          expectedManufacturing: item.expected_manufacturing || 'JUL/2025',
          defect: item.ai_finding || 'Aguardando conferência',
          reason: item.ai_finding || 'Aguardando conferência do operador.',
          aiStatus: item.ai_status || 'Em avaliação',
          aiFinding: item.ai_finding || 'Aguardando conferência',
          imageSrc: annotatedUrl,
          cropSrc: cropUrl,
          originalImageSrc: originalUrl,
          imagePath: item.image_path,
          errors,
          detected: Boolean(item.detected),
          ocrApproved: Boolean(
            item.lot_read &&
            item.manufacturing_read &&
            item.expiry_read &&
            !errors.some(error => error.codigo === 'E-MF-02')
          ),
          evaluated: Boolean(item.operator_status),
          operatorStatus: item.operator_status,
          processId: item.process_id || null,
          sequence: item.sequence || null,
          isReference: Boolean(item.is_reference),
          selectedTime: item.selected_time_s,
        }
      })

      setSamples(importedSamples)
      setStats(nextStats)

      setHistory(inspections.map(item => {
        const feedback = feedbackByInspection.get(item.id)
        const finalStatus = item.operator_status || item.ai_status

        const lot = feedback?.corrected_lot || item.lot_read || '—'
        const manufacturing = feedback?.corrected_manufacturing ||
          item.manufacturing_read || '—'
        const expiry = feedback?.corrected_expiry || item.expiry_read || '—'

        const correction = feedback
          ? `Lote: ${item.lot_read || '—'} → ${lot} | Fabricação: ${item.manufacturing_read || '—'} → ${manufacturing} | Validade: ${item.expiry_read || '—'} → ${expiry}${feedback.correction_note ? ` | Observação: ${feedback.correction_note}` : ''}`
          : '—'

        return {
          id: `IV-${String(item.id).padStart(6, '0')}`,
          lot,
          manufacturing: formatMonthYearDisplay(manufacturing),
          expiry: formatMonthYearDisplay(expiry),
          time: formatDateTime(item.reviewed_at || item.created_at),
          aiStatus: item.ai_status,
          finalStatus,
          operatorAction: item.operator_action || 'Aguardando avaliação',
          correction,
        }
      }))

      return importedSamples
    } catch (error) {
      notify(`Não foi possível carregar os dados: ${error.message}`)
      return []
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const notify = (message) => {
    setToast(message)
    window.clearTimeout(window.__ivisToastTimer)
    window.__ivisToastTimer = window.setTimeout(() => setToast(''), 2800)
  }

  const go = (id) => {
    setPage(id)
    setMobileMenu(false)
    if (id === 'inspection') loadData()
  }

  const selectSample = (id) => setSelectedId(id)

  const confirmInspection = async () => {
    if (!currentSample || currentSample.evaluated || savingInspection) return

    setSavingInspection(true)

    try {
      if (currentSample.databaseId) {
        await apiRequest(`/inspections/${currentSample.databaseId}/confirm`, {
          method: 'POST',
          body: JSON.stringify({
            operator_status: currentSample.aiStatus,
          }),
        })
      } else {
        await apiRequest('/inspections', {
          method: 'POST',
          body: JSON.stringify({
            lot_read: currentSample.lotRead,
            expected_lot: currentSample.expectedLot,
            manufacturing_read: currentSample.manufacturingRead,
            expected_manufacturing: currentSample.expectedManufacturing,
            expiry_read: currentSample.expiryRead,
            expected_expiry: currentSample.expectedExpiry,
            ai_status: currentSample.aiStatus,
            ai_finding: currentSample.aiFinding,
            image_path: currentSample.imagePath || currentSample.id,
            operator_status: currentSample.aiStatus,
            operator_action: 'Resultado confirmado pelo operador',
          }),
        })
      }

      const updatedSamples = await loadData()

      const nextSample = updatedSamples.find(item =>
        !item.evaluated && item.databaseId !== currentSample.databaseId
      )

      setLiveSample(null)
      setPage('inspection')

      if (nextSample) {
        setSelectedId(nextSample.id)
        notify(
          `Resultado confirmado. Próxima inspeção: #${String(nextSample.databaseId).padStart(4, '0')}.`
        )
      } else {
        setSelectedId(null)
        notify('Resultado confirmado. Não há mais inspeções pendentes.')
      }
    } catch (error) {
      notify(`Erro ao salvar inspeção: ${error.message}`)
    } finally {
      setSavingInspection(false)
    }
  }

  const openCorrection = () => {
    setCorrectedLot(
      ['Não legível', 'Não identificado'].includes(currentSample.lotRead)
        ? ''
        : normalizeLot(currentSample.lotRead)
    )

    setCorrectedManufacturing(
      currentSample.manufacturingRead === 'Não identificado'
        ? ''
        : currentSample.manufacturingRead || ''
    )

    setCorrectedExpiry(
      currentSample.expiryRead === 'Não identificado'
        ? ''
        : currentSample.expiryRead || ''
    )

    setCorrectedStatus(currentSample.aiStatus)
    setCorrectionNote('')
    setPage('correction')
  }

  const saveCorrection = async () => {
    if (
      !correctedLot.trim() ||
      !correctedManufacturing.trim() ||
      !correctedExpiry.trim()
    ) {
      notify('Informe o lote, a fabricação e a validade corretos.')
      return
    }

    const validDate = value => {
      const normalized = normalizeMonthYear(value)
      const match = normalized.match(/^([A-Z]{3})\/20\d{2}$/)
      return Boolean(match && MONTHS.includes(match[1]))
    }

    if (!validDate(correctedManufacturing) || !validDate(correctedExpiry)) {
      notify('Informe fabricação e validade no formato MMM/AA, por exemplo JUL/25.')
      return
    }

    try {
      const correctionData = {
        corrected_status: correctedStatus,
        corrected_lot: normalizeLot(correctedLot),
        corrected_manufacturing: normalizeMonthYear(correctedManufacturing),
        corrected_expiry: normalizeMonthYear(correctedExpiry),
        correction_note: correctionNote.trim(),
      }

      if (currentSample.databaseId) {
        await apiRequest(`/inspections/${currentSample.databaseId}/feedback`, {
          method: 'POST',
          body: JSON.stringify(correctionData),
        })
      } else {
        await apiRequest('/inspections', {
          method: 'POST',
          body: JSON.stringify({
            lot_read: currentSample.lotRead,
            expected_lot: currentSample.expectedLot,
            manufacturing_read: currentSample.manufacturingRead,
            expected_manufacturing: currentSample.expectedManufacturing,
            expiry_read: currentSample.expiryRead,
            expected_expiry: currentSample.expectedExpiry,
            ai_status: currentSample.aiStatus,
            ai_finding: currentSample.aiFinding,
            image_path: currentSample.id,
            operator_status: correctedStatus,
            operator_action: 'Corrigido pelo operador',
            ...correctionData,
          }),
        })
      }

      await loadData()
      setPage('inspection')
      setLiveSample(null)

      notify(
        `Correção salva. Resultado final: ${isConforming(correctedStatus) ? 'CONFORME' : 'NÃO CONFORME'}.`
      )
    } catch (error) {
      notify(`Erro ao salvar correção: ${error.message}`)
    }
  }

  const exportCsv = () => {
    const columns = [
      'Identificação',
      'Lote final',
      'Fabricação final',
      'Validade final',
      'Horário',
      'Resultado original da IA',
      'Resultado final',
      'Ação do operador',
      'Correção',
    ]

    const rows = history.map(item => [
      item.id,
      item.lot,
      item.manufacturing,
      item.expiry,
      item.time,
      item.aiStatus,
      item.finalStatus,
      item.operatorAction,
      item.correction,
    ])

    const csv = [columns, ...rows]
      .map(row =>
        row.map(value => `"${String(value ?? '').replaceAll('"', '""')}"`).join(';')
      )
      .join('\n')

    const blob = new Blob(['\ufeff' + csv], {
      type: 'text/csv;charset=utf-8;',
    })

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
      'Aguardando gabarito': 'warning',
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
      <div>
        <div className="eyebrow">IVIS / {title.toUpperCase()}</div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>

      <div className="header-actions">
        <button
          className="icon-button notification"
          onClick={() =>
            notify(
              pendingCount
                ? `Você tem ${pendingCount} item(ns) aguardando avaliação.`
                : 'Nenhuma notificação pendente.'
            )
          }
          aria-label="Notificações"
        >
          <Bell size={19} />
          {pendingCount > 0 && <i />}
        </button>

        <div className="operator">
          <div className="avatar">OP</div>
          <div>
            <strong>Operador</strong>
            <small>Controle de qualidade</small>
          </div>
        </div>
      </div>
    </header>
  )

  const Dashboard = () => (
    <>
      <Header
        title="Visão geral"
        subtitle="Acompanhe o desempenho das inspeções em tempo real."
      />

      <div className="welcome-banner">
        <div>
          <span className="live">
            <i /> SISTEMA OPERACIONAL
          </span>
          <h2>Olá, operador! 👋</h2>
          <p>O sistema está pronto para realizar novas inspeções.</p>
        </div>

        <button className="primary-button" onClick={() => go('inspection')}>
          <ScanSearch size={17} /> Abrir inspeção <ChevronRight size={17} />
        </button>

        <div className="banner-decoration">
          <Activity size={115} />
        </div>
      </div>

      <section className="stats-grid">
        <div className="stat-card">
          <div className="stat-top">
            <span>Inspeções registradas</span>
            <div className="stat-icon blue"><Package /></div>
          </div>
          <strong>{stats.total}</strong>
          <small><TrendingUp size={14} /> 12,8% <span>vs. período anterior</span></small>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Conformes</span>
            <div className="stat-icon green"><CheckCircle2 /></div>
          </div>
          <strong>{approvedCount}</strong>
          <small className="green-text">Itens aprovados</small>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Não conformes</span>
            <div className="stat-icon red"><XCircle /></div>
          </div>
          <strong>{rejectedCount}</strong>
          <small className="red-text">Itens reprovados</small>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Aguardando avaliação</span>
            <div className="stat-icon orange"><Clock3 /></div>
          </div>
          <strong>{pendingCount}</strong>
          <small className="orange-text">Pendentes de confirmação</small>
        </div>
      </section>

      <section className="panel recent-panel dashboard-recent">
        <div className="panel-heading">
          <div>
            <h3>Inspeções recentes</h3>
            <p>Últimos resultados confirmados ou corrigidos</p>
          </div>
          <button className="text-button" onClick={() => go('history')}>
            Ver histórico <ChevronRight size={15} />
          </button>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Identificação</th>
                <th>Horário</th>
                <th>Resultado final</th>
                <th>Ação do operador</th>
              </tr>
            </thead>
            <tbody>
              {history.slice(0, 8).map(item => (
                <tr key={item.id}>
                  <td className="mono">{item.id}</td>
                  <td>{item.time}</td>
                  <td><Status value={item.finalStatus} /></td>
                  <td>{item.operatorAction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="info-strip">
        <ShieldCheck size={19} />
        <span>
          <strong>Inspeção assistida:</strong> o operador confirma o resultado quando a IA acerta e corrige somente quando identifica um erro na análise.
        </span>
      </div>
    </>
  )

  const Inspection = () => (
  <>
        <Header
        title="Inspeção"
        subtitle="Acompanhe a análise automática e confirme ou corrija o resultado."
      />

      <section className="stats-grid inspection-stats">
        <div className="stat-card compact-stat">
          <span>Itens no lote</span>
          <strong>{stats.total}</strong>
        </div>

        <div className="stat-card compact-stat">
          <span>Conformes</span>
          <strong className="green-text">{stats.conformes}</strong>
        </div>

        <div className="stat-card compact-stat">
          <span>Não conformes</span>
          <strong className="red-text">{stats.nao_conformes}</strong>
        </div>

        <div className="stat-card compact-stat">
          <span>Aguardando avaliação</span>
          <strong>{pendingCount}</strong>
        </div>
      </section>

      <section className="panel video-upload-panel">
        <div className="panel-heading">
          <div>
            <h3>
              <Camera size={18} /> Enviar vídeo para inspeção
            </h3>
            <p>
              Selecione um vídeo da linha de produção para identificar e inspecionar
              automaticamente as embalagens.
            </p>
          </div>
        </div>

        <label className="video-upload-area">
          <input
            type="file"
            accept="video/*"
            onChange={handleVideoUpload}
            disabled={analyzing}
          />

          <div className="video-upload-icon">
            {analyzing ? <Clock3 size={30} /> : <Download size={30} />}
          </div>

          <strong>
            {analyzing ? 'Analisando vídeo...' : 'Clique para selecionar um vídeo'}
          </strong>

          <span>
            {analyzing
              ? 'Aguarde enquanto o sistema processa as embalagens.'
              : 'Formatos aceitos: MP4, AVI, MOV e outros formatos de vídeo.'}
          </span>
        </label>

        {analysisError && (
          <div className="video-upload-error">
            {analysisError}
          </div>
        )}
      </section>

      <section className="panel sample-selector">
        <div className="panel-heading">
          <div>
            <h3>Itens aguardando avaliação</h3>
            <p>Selecione um item para conferir o resultado apresentado pela IA.</p>
          </div>

          <span className="status warning">
            <span /> {pendingCount} pendentes
          </span>
        </div>

        {pendingCount > 0 && (
          <div className="sample-buttons">
            {pendingSamples.map(item => (
              <button
                key={item.databaseId}
                className={selectedId === item.id ? 'sample-button active' : 'sample-button'}
                onClick={() => {
                  setLiveSample(null)
                  selectSample(item.id)
                }}
              >
                <div className="sample-number">
                  #{String(item.databaseId).padStart(4, '0')}
                </div>

                <div className="sample-info">
                  <strong>
                    Inspeção #{item.databaseId}
                    {item.processId && ` · Processo ${item.processId} · Embalagem ${item.sequence}`}
                    {item.isReference && ' · Referência'}
                  </strong>
                  <small>{item.aiFinding}</small>
                </div>

                <Status value={item.aiStatus} />
              </button>
            ))}
          </div>
        )}
      </section>
      {currentSample && !currentSample.evaluated && (
        <>
          {currentSample.processId && processInfo?.process?.id === currentSample.processId ? (
            <GabaritoPanel
              key={`${processInfo.process.id}-${processInfo.gabarito?.updated_at}`}
              info={processInfo}
              notify={notify}
              onSaved={async () => {
                await loadData()
                await loadProcessInfo(currentSample.processId)
              }}
            />
          ) : !currentSample.processId && (
          <section className="panel reference-panel">
            <div className="panel-heading">
              <div>
                <h3><Package size={18} /> Gabarito de referência</h3>
                <p>Dados utilizados para validar as informações identificadas na embalagem.</p>
              </div>
              <span className="tag">LOTE ATUAL</span>
            </div>

            <div className="reference-values">
              <div>
                <label>LOTE DE REFERÊNCIA</label>
                <strong className="mono">{normalizeLot(currentSample.expectedLot || '100648')}</strong>
              </div>
              <div>
                <label>FABRICAÇÃO DE REFERÊNCIA</label>
                <strong>{formatMonthYearDisplay(currentSample.expectedManufacturing || 'JUL/2025')}</strong>
              </div>
              <div>
                <label>VALIDADE DE REFERÊNCIA</label>
                <strong>{formatMonthYearDisplay(currentSample.expectedExpiry || 'JUL/2027')}</strong>
              </div>
            </div>
          </section>
          )}

          <div className="inspection-layout">
            <section className="panel image-panel">
              <div className="panel-heading">
                <div>
                  <h3><Camera size={18} /> Imagem da embalagem</h3>
                  <p>Visualização da amostra selecionada</p>
                </div>
                <span className="tag">
                  AMOSTRA #{String(currentSample.databaseId || currentSample.id).padStart(4, '0')}
                </span>
              </div>

              <div
                className="package-stage dataset-stage"
                style={{
                  minHeight: 460,
                  height: 'min(62vh, 620px)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  overflow: 'hidden',
                }}
              >
                <img
                  className="dataset-image"
                  src={currentSample.imageSrc || currentSample.originalImageSrc}
                  alt="Imagem analisada pelo IVIS"
                  style={{
                    width: '100%',
                    height: '100%',
                    maxHeight: 620,
                    objectFit: 'contain',
                    display: 'block',
                  }}
                />
                <div className="scan-corner tl" />
                <div className="scan-corner tr" />
                <div className="scan-corner bl" />
                <div className="scan-corner br" />
              </div>
            </section>

            <section className="panel result-panel">
              <div className="panel-heading">
                <div>
                  <h3>Resultado da análise</h3>
                  <p>Leitura OCR e validações OCV</p>
                </div>
                <Status value={currentSample.aiStatus} />
              </div>

              <div className={`result-verdict ${awaitingGabarito ? 'pending' : isConforming(currentSample.aiStatus) ? 'approved' : 'rejected'}`}>
                {awaitingGabarito
                  ? <Clock3 size={34} />
                  : isConforming(currentSample.aiStatus)
                    ? <CheckCircle2 size={34} />
                    : <XCircle size={34} />}
                <div>
                  <span>RESULTADO DA ANÁLISE</span>
                  <strong>
                    {awaitingGabarito
                      ? 'AGUARDANDO GABARITO'
                      : isConforming(currentSample.aiStatus) ? 'CONFORME' : 'NÃO CONFORME'}
                  </strong>
                  <small>{currentSample.reason}</small>
                </div>
              </div>


              <div className="data-fields">
                <div>
                  <label>LOTE LIDO (OCR)</label>
                  <strong className="mono">
                    {formatOcrValue(currentSample.lotRead)}
                  </strong>
                </div>

                <div>
                  <label>LOTE DE REFERÊNCIA</label>
                  <strong className="mono">
                    {formatOcrValue(currentSample.expectedLot || '100648')}
                  </strong>
                </div>

                <div>
                  <label>FABRICAÇÃO LIDA (OCR)</label>
                  <strong>
                    {formatOcrValue(formatMonthYearDisplay(currentSample.manufacturingRead))}
                  </strong>
                </div>

                <div>
                  <label>FABRICAÇÃO DE REFERÊNCIA</label>
                  <strong>
                    {formatMonthYearDisplay(currentSample.expectedManufacturing || 'JUL/2025')}
                  </strong>
                </div>

                <div>
                  <label>VALIDADE LIDA (OCR)</label>
                  <strong>
                    {formatOcrValue(formatMonthYearDisplay(currentSample.expiryRead))}
                  </strong>
                </div>

                <div>
                  <label>VALIDADE DE REFERÊNCIA</label>
                  <strong>
                    {formatMonthYearDisplay(currentSample.expectedExpiry || 'JUL/2027')}
                  </strong>
                </div>
              </div>

              <div className="analysis-checks">
                <div className={currentSample.detected ? 'check-ok' : 'check-error'}>
                  {currentSample.detected ? <CheckCircle2 /> : <XCircle />}
                  {currentSample.detected ? 'Etiqueta localizada' : 'Embalagem não localizada'}
                </div>

                <div className={
                  normalizeLot(currentSample.lotRead) === normalizeLot(currentSample.expectedLot || '100648')
                    ? 'check-ok'
                    : 'check-error'
                }>
                  {normalizeLot(currentSample.lotRead) === normalizeLot(currentSample.expectedLot || '100648')
                    ? <CheckCircle2 />
                    : <XCircle />}
                  {normalizeLot(currentSample.lotRead) === normalizeLot(currentSample.expectedLot || '100648')
                    ? 'Lote corresponde ao gabarito'
                    : 'Lote divergente do gabarito'}
                </div>

                <div className={
                  normalizeMonthYear(currentSample.manufacturingRead) === normalizeMonthYear(currentSample.expectedManufacturing || 'JUL/2025')
                    ? 'check-ok'
                    : 'check-error'
                }>
                  {normalizeMonthYear(currentSample.manufacturingRead) === normalizeMonthYear(currentSample.expectedManufacturing || 'JUL/2025')
                    ? <CheckCircle2 />
                    : <XCircle />}
                  {normalizeMonthYear(currentSample.manufacturingRead) === normalizeMonthYear(currentSample.expectedManufacturing || 'JUL/2025')
                    ? 'Fabricação corresponde ao gabarito'
                    : 'Fabricação ilegível ou divergente'}
                </div>

                <div className={
                  normalizeMonthYear(currentSample.expiryRead) === normalizeMonthYear(currentSample.expectedExpiry || 'JUL/2027')
                    ? 'check-ok'
                    : 'check-error'
                }>
                  {normalizeMonthYear(currentSample.expiryRead) === normalizeMonthYear(currentSample.expectedExpiry || 'JUL/2027')
                    ? <CheckCircle2 />
                    : <XCircle />}
                  {normalizeMonthYear(currentSample.expiryRead) === normalizeMonthYear(currentSample.expectedExpiry || 'JUL/2027')
                    ? 'Validade corresponde ao gabarito'
                    : 'Validade ilegível ou divergente'}
                </div>

                <div className={currentSample.ocrApproved ? 'check-ok' : 'check-error'}>
                  {currentSample.ocrApproved ? <CheckCircle2 /> : <AlertTriangle />}
                  {currentSample.ocrApproved
                    ? 'Verificação de formato executada'
                    : 'OCR inconclusivo'}
                </div>
              </div>

              <div className="operator-feedback">
                <h4>Avaliação do operador</h4>
                <p>
                  {currentSample.reason} Se a análise estiver correta, confirme.
                  Se a IA tiver errado, solicite a correção.
                </p>

                <div className="inspection-actions">
                  <button
                    className="primary-button"
                    onClick={confirmInspection}
                    disabled={currentSample.evaluated || savingInspection || awaitingGabarito}
                    title={awaitingGabarito ? 'Confirme o gabarito do processo antes' : undefined}
                  >
                    {savingInspection ? <Clock3 size={17} /> : <Check size={17} />}
                    {savingInspection ? 'Salvando...' : 'Confirmar resultado'}
                  </button>

                  <button className="secondary-button correction-action" onClick={openCorrection}>
                    <AlertTriangle size={16} /> Corrigir resultado
                  </button>
                </div>

                <small className="action-hint">
                  {awaitingGabarito && <><b>Confirme o gabarito do processo acima para liberar a confirmação.</b>{' '}</>}
                  Confirmar mantém o veredito da IA. Corrigir abre uma tela separada
                  para ajustar os dados e o resultado final.
                </small>
              </div>
            </section>
          </div>
        </>
      )}
    </>
  )

  const Correction = () => (
    <>
      <Header
        title="Correção pelo operador"
        subtitle="Ajuste a leitura e o veredito quando identificar um erro na análise automática."
      />

      <button className="back-link" onClick={() => go('inspection')}>
        <ArrowLeft size={16} /> Voltar para inspeção
      </button>

      <div className="correction-intro">
        <div className="correction-intro-icon">
          <AlertTriangle size={20} />
        </div>
        <div>
          <strong>Correção da amostra #{currentSample.id}</strong>
          <p>
            Resultado original da IA: <b>{isConforming(currentSample.aiStatus) ? 'CONFORME' : 'NÃO CONFORME'}</b>
            {' '}— {currentSample.aiFinding}. Altere somente o que estiver incorreto.
          </p>
        </div>
      </div>

      <div className="inspection-layout correction-layout">
        <section className="panel image-panel">
          <div className="panel-heading">
            <div>
              <h3><Camera size={18} /> Imagem da embalagem</h3>
              <p>Use a imagem como referência para a correção.</p>
            </div>
            <span className="tag">AMOSTRA #{currentSample.id}</span>
          </div>

          <div
            className="package-stage dataset-stage"
            style={{
              minHeight: 460,
              height: 'min(62vh, 620px)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              overflow: 'hidden',
            }}
          >
            <img
              className="dataset-image"
              src={currentSample.imageSrc || currentSample.originalImageSrc}
              alt={`Imagem da amostra ${currentSample.id} do IVIS`}
              style={{
                width: '100%',
                height: '100%',
                maxHeight: 620,
                objectFit: 'contain',
                display: 'block',
              }}
            />
            <div className="scan-corner tl" />
            <div className="scan-corner tr" />
            <div className="scan-corner bl" />
            <div className="scan-corner br" />
          </div>
        </section>

        <section className="panel result-panel">
          <div className="panel-heading">
            <div>
              <h3>Dados identificados pela IA</h3>
              <p>Compare a leitura original com a embalagem e o gabarito.</p>
            </div>
            <Status value={currentSample.aiStatus} />
          </div>

          <div className="data-fields">
            <div>
              <label>LOTE LIDO PELA IA</label>
              <strong className="mono">{currentSample.lotRead}</strong>
            </div>
            <div>
              <label>LOTE DE REFERÊNCIA</label>
              <strong className="mono">{currentSample.expectedLot}</strong>
            </div>
            <div>
              <label>FABRICAÇÃO LIDA PELA IA</label>
              <strong>{formatMonthYearDisplay(currentSample.manufacturingRead)}</strong>
            </div>
            <div>
              <label>FABRICAÇÃO DE REFERÊNCIA</label>
              <strong>{formatMonthYearDisplay(currentSample.expectedManufacturing)}</strong>
            </div>
            <div>
              <label>VALIDADE LIDA PELA IA</label>
              <strong>{formatMonthYearDisplay(currentSample.expiryRead)}</strong>
            </div>
            <div>
              <label>VALIDADE DE REFERÊNCIA</label>
              <strong>{formatMonthYearDisplay(currentSample.expectedExpiry)}</strong>
            </div>
          </div>

          <div className="correction-form">
            <h3><FileCheck2 size={17} /> Correção dos dados</h3>
            <p>Informe os valores corretos conforme a embalagem.</p>

            <div className="correction-fields">
              <label>
                Lote correto
                <input
                  value={correctedLot}
                  onChange={e => setCorrectedLot(e.target.value)}
                  placeholder="Digite o lote correto"
                />
              </label>

              <label>
                Fabricação correta
                <input
                  value={formatMonthYearInput(correctedManufacturing)}
                  onChange={e => setCorrectedManufacturing(formatMonthYearInput(e.target.value))}
                  placeholder="MMM/AA"
                  maxLength={6}
                />
              </label>

              <label>
                Validade correta
                <input
                  value={formatMonthYearInput(correctedExpiry)}
                  onChange={e => setCorrectedExpiry(formatMonthYearInput(e.target.value))}
                  placeholder="MMM/AA"
                  maxLength={6}
                />
              </label>
            </div>

            <label className="decision-label status-select-label">
              Veredito correto da embalagem
              <select
                value={correctedStatus}
                onChange={e => setCorrectedStatus(e.target.value)}
              >
                <option value="Conforme">CONFORME — embalagem correta</option>
                <option value="Não Conforme">NÃO CONFORME — embalagem com defeito</option>
              </select>
            </label>

            <label className="decision-label note-field">
              Observação da correção
              <textarea
                value={correctionNote}
                onChange={e => setCorrectionNote(e.target.value)}
                placeholder="Descreva o erro da IA ou a correção realizada (opcional)."
                rows={3}
              />
            </label>

            <div className="correction-actions">
              <button className="secondary-button" onClick={() => go('inspection')}>
                Cancelar
              </button>
              <button className="primary-button" onClick={saveCorrection}>
                <Save size={16} /> Salvar correção e feedback
              </button>
            </div>
          </div>
        </section>
      </div>
    </>
  )

  const filteredHistory = useMemo(
    () =>
      filter === 'Todos'
        ? history
        : history.filter(item =>
            filter === 'Aprovado'
              ? isConforming(item.finalStatus)
              : !isConforming(item.finalStatus)
          ),
    [filter, history]
  )

  const History = () => (
    <>
      <Header
        title="Histórico de inspeções"
        subtitle="Consulte os resultados originais, confirmações e correções registradas."
      />

      <section className="panel history-panel">
        <div className="panel-heading">
          <div>
            <h3>Registros de inspeção</h3>
            <p>{history.length} registros salvos no banco local</p>
          </div>

          <div className="history-actions">
            <label className="filter-control">
              <Filter size={15} />
              <select value={filter} onChange={e => setFilter(e.target.value)}>
                <option>Todos</option>
                <option value="Aprovado">Conforme</option>
                <option value="Reprovado">Não conforme</option>
              </select>
            </label>

            <button className="secondary-button" onClick={exportCsv}>
              <Download size={16} /> Exportar CSV
            </button>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Identificação</th>
                <th>Lote final</th>
                <th>Fabricação final</th>
                <th>Validade final</th>
                <th>Horário</th>
                <th>Resultado IA</th>
                <th>Resultado final</th>
                <th>Ação do operador</th>
                <th>Correção</th>
              </tr>
            </thead>

            <tbody>
              {filteredHistory.map(item => (
                <tr key={item.id}>
                  <td className="mono">{item.id}</td>
                  <td className="mono">{item.lot}</td>
                  <td>{formatMonthYearDisplay(item.manufacturing)}</td>
                  <td>{formatMonthYearDisplay(item.expiry)}</td>
                  <td>{item.time}</td>
                  <td><Status value={item.aiStatus} /></td>
                  <td><Status value={item.finalStatus} /></td>
                  <td>{item.operatorAction}</td>
                  <td className="correction-cell">{item.correction}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {filteredHistory.length === 0 && (
            <div className="empty-state">
              Nenhum registro encontrado para este filtro.
            </div>
          )}
        </div>
      </section>

      <div className="info-strip">
        <CalendarDays size={19} />
        <span>
          O histórico diferencia o resultado original da IA do resultado final
          confirmado ou corrigido pelo operador.
        </span>
      </div>
    </>
  )

  // As páginas são chamadas como funções (e não como <Componente />) porque são
  // definidas dentro de App: como componente, cada digitação recriava a página
  // inteira e o campo de texto perdia o foco a cada letra.
  const content =
  page === 'dashboard'
    ? Dashboard()
    : page === 'inspection'
      ? Inspection()
      : page === 'correction'
        ? (currentSample ? Correction() : Inspection())
        : History()

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenu ? 'mobile-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark">
            <Activity size={23} />
          </div>

          <div>
            <strong>IVIS<span>.</span></strong>
            <small>INTELLIGENT INSPECTION</small>
          </div>

          <button className="mobile-close" onClick={() => setMobileMenu(false)}>
            <X />
          </button>
        </div>

        <div className="side-label">MENU PRINCIPAL</div>

        <nav>
          {navItems.map(item => {
            const Icon = item.icon
            const active =
              page === item.id ||
              (page === 'correction' && item.id === 'inspection')

            return (
              <button
                key={item.id}
                className={active ? 'nav-item active' : 'nav-item'}
                onClick={() => go(item.id)}
              >
                <Icon size={19} />
                <span>{item.label}</span>
                {active && <i />}
              </button>
            )
          })}
        </nav>

        <div className="sidebar-bottom">
          <div className="offline">
            <span /> SISTEMA OPERACIONAL
          </div>
          <div className="side-version">
            IVIS <span>v0.3.0</span>
          </div>
        </div>
      </aside>

      {mobileMenu && (
        <div
          className="mobile-overlay"
          onClick={() => setMobileMenu(false)}
        />
      )}

      <main className="main-content">
        {loading && (
          <div className="info-strip">
            <Clock3 size={18} />
            <span>Carregando dados do banco local...</span>
          </div>
        )}

        <div className="mobile-top">
          <button onClick={() => setMobileMenu(true)}>
            <Menu />
          </button>
          <strong>IVIS<span>.</span></strong>
        </div>

        {content}
      </main>

      {toast && (
        <div className="toast">
          <CheckCircle2 size={17} />
          {toast}
        </div>
      )}
    </div>
  )
}

export default App