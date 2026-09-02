# TODO - Correção de Duplicatas nos Resultados

## Etapas:

- [x] 1. **merge_cpf**: Corrigir lógica de merge para evitar campos duplicados com mesma chave canônica
- [x] 2. **flatten_dict**: Melhorar deduplicação com `seen` set
- [x] 3. **handle_message**: Remover envio automático do menu após cada consulta
- [ ] 4. **format_result**: Adicionar deduplicação na lista de resultados
- [ ] 5. Testar e verificar
