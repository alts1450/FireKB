import { useState, useRef, useEffect } from 'react'
import './ProcessControl.css'

interface ProcessControlProps {
  materials: string[]
  isProcessing: boolean
  onProcessingChange: (processing: boolean) => void
}

export default function ProcessControl({ materials, isProcessing, onProcessingChange }: ProcessControlProps) {
  const [logs, setLogs] = useState<string[]>([])
  const [currentStep, setCurrentStep] = useState<string>('')
  const logsContentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const append = (data: string) => setLogs(prev => [...prev, data].slice(-1200))
    const offOutput = window.electronAPI.onOutput(append)
    const offError = window.electronAPI.onError(data => append(`❌ ${data}`))
    return () => { offOutput(); offError() }
  }, [])

  useEffect(() => {
    const panel = logsContentRef.current
    if (panel) panel.scrollTop = panel.scrollHeight
  }, [logs])

  const addLog = (message: string) => {
    setLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${message}`].slice(-1200))
  }

  const handleParse = async () => {
    if (materials.length === 0) {
      addLog('❌ 请先添加素材文件')
      return
    }

    onProcessingChange(true)
    setCurrentStep('parse')
    addLog('开始解析文档...')

    try {
      const result = await window.electronAPI.runCommand('parse')
      if (result.success) {
        addLog('✅ 文档解析完成')
      } else {
        addLog(`❌ 解析失败: ${result.stderr}`)
      }
    } catch (error) {
      addLog(`❌ 执行出错: ${error}`)
    } finally {
      onProcessingChange(false)
      setCurrentStep('')
    }
  }

  const handleOCR = async () => {
    onProcessingChange(true)
    setCurrentStep('ocr')
    addLog('开始 OCR 识别...')

    try {
      const result = await window.electronAPI.runCommand('ocr', ['--check'])
      if (!result.success) {
        addLog(`❌ OCR 检查失败: ${result.stderr}`)
        return
      }

      if (confirm('确认执行 OCR 识别？这可能需要较长时间。')) {
        const ocrResult = await window.electronAPI.runCommand('ocr')
        if (ocrResult.success) {
          addLog('✅ OCR 识别完成')
        } else {
          addLog(`❌ OCR 失败: ${ocrResult.stderr}`)
        }
      }
    } catch (error) {
      addLog(`❌ 执行出错: ${error}`)
    } finally {
      onProcessingChange(false)
      setCurrentStep('')
    }
  }

  const handleBuildSkeleton = async () => {
    onProcessingChange(true)
    setCurrentStep('skeleton')
    addLog('开始构建知识骨架...')

    try {
      const result = await window.electronAPI.runCommand('skeleton')
      if (result.success) {
        addLog('✅ 知识骨架构建完成')
      } else {
        addLog(`❌ 构建失败: ${result.stderr}`)
      }
    } catch (error) {
      addLog(`❌ 执行出错: ${error}`)
    } finally {
      onProcessingChange(false)
      setCurrentStep('')
    }
  }

  const handleExtract = async () => {
    onProcessingChange(true)
    setCurrentStep('extract')
    addLog('开始抽取知识点（试跑 3 条）...')

    try {
      const result = await window.electronAPI.runCommand('extract', ['--limit', '3'])
      if (result.success) {
        addLog('✅ 知识点抽取完成（试跑）')
        if (confirm('试跑成功，是否执行全量抽取？这将消耗 API 额度。')) {
          addLog('开始全量抽取...')
          const fullResult = await window.electronAPI.runCommand('extract')
          if (fullResult.success) {
            addLog('✅ 全量抽取完成')
          } else {
            addLog(`❌ 全量抽取失败: ${fullResult.stderr}`)
          }
        }
      } else {
        addLog(`❌ 抽取失败: ${result.stderr}`)
      }
    } catch (error) {
      addLog(`❌ 执行出错: ${error}`)
    } finally {
      onProcessingChange(false)
      setCurrentStep('')
    }
  }

  const handleVerify = async () => {
    onProcessingChange(true)
    setCurrentStep('verify')
    addLog('开始校验知识点...')

    try {
      const result = await window.electronAPI.runCommand('verify')
      if (result.success) {
        addLog('✅ 知识点校验完成')
      } else {
        addLog(`❌ 校验失败: ${result.stderr}`)
      }
    } catch (error) {
      addLog(`❌ 执行出错: ${error}`)
    } finally {
      onProcessingChange(false)
      setCurrentStep('')
    }
  }

  const handleClearLogs = () => {
    setLogs([])
  }

  const steps = [
    { id: 'parse', label: '1. 解析文档', handler: handleParse, description: '提取 PDF/PPTX/EPUB 的文本内容' },
    { id: 'ocr', label: '2. OCR 识别', handler: handleOCR, description: '识别影印版文档的文字（可选）' },
    { id: 'skeleton', label: '3. 构建骨架', handler: handleBuildSkeleton, description: '生成课程章节结构' },
    { id: 'extract', label: '4. 抽取知识点', handler: handleExtract, description: '使用 AI 提取知识点' },
    { id: 'verify', label: '5. 校验验证', handler: handleVerify, description: '核验引用和数据完整性' }
  ]

  return (
    <div className="process-control">
      <div className="steps-panel">
        <h3 className="panel-title">处理流程</h3>
        <p className="panel-subtitle">按顺序执行；素材须先放入项目归档目录。重建骨架会覆盖既有结构与人工复核。</p>

        <div className="steps-list">
          {steps.map(step => (
            <div key={step.id} className="step-card">
              <div className="step-content">
                <h4 className="step-title">{step.label}</h4>
                <p className="step-description">{step.description}</p>
              </div>
              <button
                className="btn btn-primary"
                onClick={step.handler}
                disabled={isProcessing}
              >
                {isProcessing && currentStep === step.id ? '处理中…' : '执行'}
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="logs-panel">
        <div className="logs-header">
          <h3 className="panel-title">执行日志</h3>
          <button className="btn-text" onClick={handleClearLogs}>
            清空日志
          </button>
        </div>

        <div className="logs-content" ref={logsContentRef} role="log" aria-label="执行日志" aria-live="off">
          {logs.length === 0 ? (
            <p className="logs-empty">暂无日志</p>
          ) : (
            <>
              {logs.map((log, index) => (
                <div key={index} className={`log-entry ${log.includes('❌') ? 'log-error' : log.includes('✅') ? 'log-success' : ''}`}>
                  {log}
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
