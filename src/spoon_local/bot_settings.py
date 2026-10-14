"""Single local simulator profile, versioned SQLite settings; never tokens or run state."""
import json,sqlite3
from pathlib import Path
from spoon_local.simulator_automation import Automation
from spoon_local.spoon.event_models import utc_now

class SettingsConflict(ValueError):pass

class BotSettingsStore:
    def __init__(self,path=None):
        self.persistent=path is not None
        if path is not None:
            path=Path(path)
            if not path.is_absolute() or path.name!='bot-settings.sqlite3' or path.is_symlink():raise ValueError('Dedicated absolute bot-settings.sqlite3 required')
            path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path) if path else ':memory:')
        existing={row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if existing and existing!={'bot_settings'}:
            self.db.close();raise ValueError('Unrecognized settings database')
        try:
            self.db.execute('CREATE TABLE IF NOT EXISTS bot_settings (id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL, value TEXT NOT NULL)')
            self.db.execute('INSERT OR IGNORE INTO bot_settings VALUES (1,1,1,?,?)',(utc_now(),json.dumps(Automation().settings,ensure_ascii=False)))
            self.db.commit();self.read()
            if path:path.chmod(0o600)
        except Exception:self.db.close();raise
    def read(self):
        row=self.db.execute('SELECT schema_version,version,updated_at,value FROM bot_settings WHERE id=1').fetchone()
        if not row or row[0]!=1:raise ValueError('Unsupported settings schema')
        values=json.loads(row[3]);Automation().configure(values)
        return {'settings':values,'schema_version':1,'version':row[1],'updated_at_utc':row[2],'persistent':self.persistent,'ownership':'local_simulator_profile_not_dj_identity'}
    def save(self,values,version):
        if type(version) is not int or version<1:raise ValueError()
        Automation().configure(values)
        with self.db:
            changed=self.db.execute('UPDATE bot_settings SET value=?, version=version+1,updated_at=? WHERE id=1 AND version=?',(json.dumps(values,ensure_ascii=False),utc_now(),version)).rowcount
            if changed!=1:raise SettingsConflict()
        return self.read()
    def close(self):self.db.close()
