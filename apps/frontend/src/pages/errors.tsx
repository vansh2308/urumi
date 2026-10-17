import { FileQuestionIcon, ShieldOffIcon } from "lucide-react"
import { Link } from "react-router"

import { PageBody } from "@/components/layout/app-shell"
import { EmptyState } from "@/components/common/query-state"
import { Button } from "@/components/ui/button"

export function ForbiddenPage() {
  return (
    <PageBody>
      <EmptyState
        icon={<ShieldOffIcon />}
        title="You don't have access to this page"
        description="Your role in this team does not include it. Ask a team admin if you need access."
        action={
          <Button asChild variant="outline">
            <Link to="/overview">Back to overview</Link>
          </Button>
        }
      />
    </PageBody>
  )
}

export function NotFoundPage() {
  return (
    <PageBody>
      <EmptyState
        icon={<FileQuestionIcon />}
        title="Page not found"
        description="The page you are looking for does not exist or was moved."
        action={
          <Button asChild variant="outline">
            <Link to="/overview">Back to overview</Link>
          </Button>
        }
      />
    </PageBody>
  )
}
