import settings from '../../../branding/brand.json'

export const brand = Object.freeze(settings)
export const productName = brand.productName
export const productSlug = brand.productSlug
export const productDescription = `${productName} ${brand.description}`
export const helpUrl = (_section?: string): string => brand.links.help
export const isProviderHidden = (provider: string): boolean => brand.hiddenProviders.includes(provider.toLowerCase())
