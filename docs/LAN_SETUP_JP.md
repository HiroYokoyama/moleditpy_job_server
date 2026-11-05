# 別の PC から接続する

Job Server に別の PC から接続するための設定手順です。[English](LAN_SETUP.md)。
設計やセキュリティの詳細は [LAN_ACCESS.md](LAN_ACCESS.md) と [SECURITY.md](../SECURITY.md) を
参照してください。

例では次の値を使っています。実際の値に置き換えてください。

| 項目 | 例 |
|---|---|
| サーバー PC の LAN IP | `192.168.1.20` |
| HTTP ポート(既定) | `8766` |
| HTTPS ポート(既定) | `8767` |
| Tailscale の名前 | `your-pc.your-tailnet.ts.net` |

サーバー PC に Job Server 0.6.0 以上、接続元の PC に Job Manager 3.9.0 以上が必要です。
Job Server 0.5.0 は `127.0.0.1` でしか待ち受けません。

## 接続方法を選ぶ

| 接続方法 | URL | ファイアウォールのルール | 通信 | 向いている場面 |
|---|---|---|---|---|
| ローカル HTTPS(おすすめ) | `https://192.168.1.20:8767` | 必要 | 暗号化される | 両方の PC が同じ LAN にある |
| Tailscale | `https://your-pc.your-tailnet.ts.net` | 不要 | 暗号化される | 両方の PC が同じ tailnet にある、またはファイアウォールを変更できない |
| LAN HTTP | `http://192.168.1.20:8766` | 必要 | **暗号化されない** | 信頼できる LAN で、HTTPS を使えない |

スキームとポートは組み合わせを合わせてください。`8767` は HTTPS 専用なので、
`http://192.168.1.20:8767` では接続できません。

## ローカル HTTPS

### サーバー PC での設定

1. **Job Server > Settings** を開きます。
2. **Enable local HTTPS with a persistent local CA** を有効にします。
3. **LAN listen address** で、この PC の LAN IP を選びます。
4. 保存して **Server > Restart** を選びます。サーバーが止まっている場合は **Start** です。
5. **Create / show CA certificate...** を開き、SHA-256 指紋をコピーします。
6. LAN IP で待ち受けていることを確認します。

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 8766,8767 | Select-Object LocalAddress,LocalPort
   ```

   一覧に `192.168.1.20` が必要です。`127.0.0.1` だけなら、手順 2〜4 を確認してください。

7. **管理者**の PowerShell を開き、ネットワークプロファイルを確認します。

   ```powershell
   Get-NetConnectionProfile | Select-Object InterfaceAlias,NetworkCategory
   ```

8. LAN のアダプターが `Public` で、信頼できるネットワークなら `Private` に変更します。
   アダプター名は手順 7 で表示された名前にしてください。

   ```powershell
   Set-NetConnectionProfile -InterfaceAlias "イーサネット" -NetworkCategory Private
   ```

9. ポートを許可します。プロファイルを変更しただけでは開きません。

   ```powershell
   New-NetFirewallRule -Name "MoleditPy-Job-Server-LAN-HTTPS" -DisplayName "Job Server LAN HTTPS" -Direction Inbound -Action Allow -Protocol TCP -LocalAddress 192.168.1.20 -LocalPort 8767 -RemoteAddress LocalSubnet -Profile Private
   ```

### 接続元の PC での設定

1. ポートに届くことを確認します。`-k` は証明書の検証を省略するので、認証情報を送らない
   この確認にだけ使ってください。

   ```powershell
   curl.exe -k https://192.168.1.20:8767/api/v1/health
   ```

   応答に `"status":"ok"` が含まれていれば接続できています。

2. Job Manager のホスト設定を開き、**Backend** を **Job Server (HTTPS)** にします。
3. **Server URL** に `https://192.168.1.20:8767` を入れます。
4. **Trust local server CA...** を選び、サーバー PC の指紋を貼り付けます。
5. **Sign in to Job Server...** を選び、ユーザー名とパスワードを入れます。

## Tailscale

両方の PC が同じ tailnet にサインインしている必要があります。使うのは非公開の Tailscale Serve
だけにしてください。Funnel やルーターのポート転送は使わないでください。

1. サーバー PC で次を実行し、表示された URL を控えます。

   ```powershell
   tailscale serve --bg http://127.0.0.1:8766
   ```

2. その URL を **Job Server > Settings > Web access URLs** に追加し、保存して再起動します。
3. Job Manager の **Server URL** に `https://your-pc.your-tailnet.ts.net` を入れます。
4. **Sign in to Job Server...** を選びます。公開された正規の証明書を使うので、
   **Trust local server CA...** は不要です。

## LAN HTTP

パスワード、トークン、ジョブの内容は、LAN の通信を傍受できる人に読まれます。信頼できる LAN でだけ
使ってください。

1. サーバー PC で **Allow trusted LAN HTTP** を有効にし、**LAN listen address** を選んで、
   保存して再起動します。
2. ローカル HTTPS の手順 7〜9 を、ポート `8766` と別のルール名で実行します。
3. Job Manager の **Server URL** に `http://192.168.1.20:8766` を入れます。
4. **Allow trusted LAN HTTP (unencrypted)** を有効にします。
5. **Sign in to Job Server...** を選びます。

## トラブルシューティング

| 症状 | 考えられる原因 | 対処 |
|---|---|---|
| ping は通るがポートがタイムアウトする | サーバー PC のファイアウォール、または LAN で待ち受けていない | 待ち受けを確認してから、ファイアウォールのルールを追加する |
| プロファイルを `Private` にしてもタイムアウトする | そのポートのルールがない | ルールを追加する。プロファイルの変更だけでは開かない |
| ルールがあるのにタイムアウトする | ルールのプロファイルが合っていない、グループポリシー、別のセキュリティソフト | ルールとプロファイルを確認し、管理者に相談する |
| `127.0.0.1` でしか待ち受けていない | LAN の待ち受けがオフ、またはサーバーが 0.6.0 より古い | Settings で有効にして再起動するか、更新する |
| `Set-NetConnectionProfile` が権限エラーになる | 管理者でない、ドメインネットワーク、グループポリシー | 管理者で再実行する。それでも失敗するなら管理者に相談するか Tailscale を使う |
| `8767` への `http://` が失敗する | このポートは HTTPS 専用 | `https://` を使うか、`http://` ならポート `8766` を使う |
| 証明書エラーになる | CA が信頼されていない、または IP が変わった | **Trust local server CA...** をやり直す。IP が変わったら URL も更新する |
| ブラウザーで `origin_forbidden` になる | そのアドレスが登録されていない | 正確な origin を **Web access URLs** に追加して再起動する |

サーバー PC を組織が管理している場合は、ネットワークプロファイルやファイアウォールのルールを変更する前に
管理者に確認してください。
