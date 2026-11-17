import { shallowRef, watch } from 'vue'
import type { Ref } from 'vue'
import { getPdmOptions } from '../api/pdmStatistics'
import { clonePdmFilters } from '../utils/pdmFilters'
import type { PdmDocumentKind, PdmFilterOptions, PdmFilters } from '../utils/pdmFilters'

export function usePdmFilterOptions(draft: Ref<PdmFilters>, visible: Ref<boolean>, kind: PdmDocumentKind) {
  const options = shallowRef<PdmFilterOptions>({})
  const status = shallowRef<'loading' | 'ready' | 'error'>('loading')
  const error = shallowRef('')
  const revision = shallowRef(0)
  let previousScope: string | undefined
  const scope = () => {
    const branch = draft.value[kind]
    return JSON.stringify([branch.types, branch.projects, branch.professions])
  }
  watch(() => JSON.stringify([visible.value, scope(), revision.value]), async (_, __, onCleanup) => {
    if (!visible.value) { previousScope = undefined; return }
    const controller = new AbortController()
    let active = true
    onCleanup(() => { active = false; controller.abort() })
    status.value = 'loading'
    error.value = ''
    const requestedScope = scope()
    try {
      const result = await getPdmOptions(kind, clonePdmFilters(draft.value)[kind], controller.signal)
      if (!active) return
      const next = clonePdmFilters(draft.value)
      const branch = next[kind]
      branch.projects = branch.projects.filter(id => result.projects?.some(option => option.value === id))
      branch.professions = branch.professions.filter(id => result.professions?.some(option => option.value === id))
      const people = result.responsible_people?.map(option => option.value) || []
      branch.responsiblePeople = branch.responsiblePeople !== null && previousScope === requestedScope
        ? branch.responsiblePeople.filter(value => people.includes(value)) : people
      previousScope = requestedScope
      options.value = { [kind]: result }
      draft.value = next
      status.value = 'ready'
    } catch (caught) {
      if (!active) return
      error.value = caught instanceof Error ? caught.message : String(caught)
      status.value = 'error'
    }
  }, { immediate: true })
  return { options, status, error, retry: () => { revision.value++ } }
}
