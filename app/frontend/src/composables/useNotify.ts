import { useMessage } from 'naive-ui'

/** 统一的轻量提示封装（基于 Naive UI message） */
export function useNotify() {
  const message = useMessage()
  return {
    ok: (msg: string) => message.success(msg),
    err: (msg: string) => message.error(msg, { duration: 5000 }),
    info: (msg: string) => message.info(msg),
  }
}
