import { SecretRevealDialog } from "@/components/common/secret-reveal-dialog"
import type { CreatedStore } from "@/lib/api/types"

/** The wp-admin password, shown once right after the store is requested. */
export function AdminPasswordDialog({
  store,
  onClose,
}: {
  store: CreatedStore | null
  onClose: () => void
}) {
  return (
    <SecretRevealDialog
      secret={store?.admin_password ?? null}
      title={`wp-admin password for ${store?.name ?? "the store"}`}
      description={
        <>
          Sign in at <span className="font-mono">{store?.admin_url}</span> as{" "}
          <span className="font-mono">{store?.admin_username}</span> once the store is ready.
        </>
      }
      notice="Copy it now. The platform keeps it only in the store's own Kubernetes Secret and cannot show it again."
      onClose={onClose}
    />
  )
}
