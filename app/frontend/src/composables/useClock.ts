import { onMounted, onUnmounted, ref } from 'vue'
import { fmtBerlin } from './format'

/** 每秒更新的柏林时间字符串 */
export function useClock() {
  const clock = ref('')
  let timer: ReturnType<typeof setInterval> | undefined

  const tick = () => {
    clock.value = `${fmtBerlin(new Date(), true)} CET`
  }

  onMounted(() => {
    tick()
    timer = setInterval(tick, 1000)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { clock }
}
