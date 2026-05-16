import { useState } from "react";
import { FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function StepFolder({
  value,
  onChange,
  onBack,
  onNext,
}: {
  value: string;
  onChange: (v: string) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const [v, setV] = useState(value);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Source folder</CardTitle>
        <CardDescription>
          Absolute path to a folder of medical Markdown files (textbook chapters, clinical notes,
          guidelines). The backend will recursively scan for <code>*.md</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="path">Folder path</Label>
          <div className="flex gap-2">
            <FolderOpen className="mt-2 h-5 w-5 text-muted-foreground" />
            <Input
              id="path"
              placeholder="/Users/you/medical-textbooks"
              value={v}
              onChange={(e) => setV(e.target.value)}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Tip: paste a path like <code>/Users/ahora/Desktop/Projects/IUMS/text2graph/articles_md_sample</code>.
          </p>
        </div>
        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>Back</Button>
          <Button
            onClick={() => {
              onChange(v.trim());
              onNext();
            }}
            disabled={!v.trim()}
          >
            Continue
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
