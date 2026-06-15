import { defineStore } from 'pinia'
import { ref } from 'vue'
import { groupsApi } from '@/api/endpoints'
import type { Group } from '@/api/types'

/** 分组列表是多个页面共享的下拉数据源，集中缓存 */
export const useGroupsStore = defineStore('groups', () => {
  const groups = ref<Group[]>([])
  const loaded = ref(false)

  async function load(force = false) {
    if (loaded.value && !force) return groups.value
    groups.value = await groupsApi.list()
    loaded.value = true
    return groups.value
  }

  function symbolsOf(g: Group): Array<{ ticker: string; t212_ticker?: string; tags?: string[] }> {
    return (g.config?.symbols ?? []).map((s) => ({
      ticker: s.ticker || s.symbol || '',
      t212_ticker: s.t212_ticker,
      tags: s.tags,
    }))
  }

  return { groups, loaded, load, symbolsOf }
})
