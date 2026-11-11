import katex from 'katex'
import 'katex/dist/katex.min.css'

export default function MathText({ text }: { text: string }) {
  return <>{text.split(/(\$[^$]+\$)/g).map((part, index) => {
    if (!part.startsWith('$')) return <span key={index}>{part}</span>
    try {
      return <span key={index} style={{ display: 'inline-block', maxWidth: '100%', verticalAlign: 'baseline' }} dangerouslySetInnerHTML={{ __html: katex.renderToString(part.slice(1, -1), { throwOnError: true, trust: false }) }} />
    } catch {
      return <span key={index} title="公式语法需检查">{part}</span>
    }
  })}</>
}
