"""Verify all rows existing at backup time remain unchanged; allow new rows/tables."""
import sqlite3,sys,json

def verify(before,after):
    with sqlite3.connect(before) as old,sqlite3.connect(after) as current:
        assert current.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        checked=0
        for (table,) in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columns=[r[1] for r in old.execute('PRAGMA table_info("'+table+'")')]
            quoted=','.join('"'+x+'"' for x in columns)
            # Existing schema uses rowid tables; added records never replace an old rowid.
            for row in old.execute('SELECT rowid,'+quoted+' FROM "'+table+'"'):
                actual=current.execute('SELECT '+quoted+' FROM "'+table+'" WHERE rowid=?',(row[0],)).fetchone()
                assert actual==row[1:],'Pre-existing row changed: '+table
                checked+=1
        return {'legacy_rows_unchanged':True,'rows_verified':checked,'integrity':'ok'}
if __name__=='__main__':print(json.dumps(verify(*sys.argv[1:3])))
