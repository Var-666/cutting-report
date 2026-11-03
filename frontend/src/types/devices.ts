import type { PublicLineCatalog, PublicProductionLine } from '../api/generated/types.gen'

export type ProductionLine = PublicProductionLine
export type DeviceCatalog = PublicLineCatalog
export type CuttingDevice = PublicProductionLine & { type: 'cutting' }
export type WeldingLine = PublicProductionLine & { type: 'welding' }
